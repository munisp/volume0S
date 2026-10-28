use std::collections::HashMap;
use std::sync::Arc;

use jsonwebtoken::{decode, decode_header, Algorithm, DecodingKey, Validation};
use serde::Deserialize;
use tokio::sync::RwLock;

#[derive(Clone, Debug, Deserialize)]
pub struct Claims {
    pub tenant_id: String,
    pub actor: String,
    pub role: String,
    #[allow(dead_code)]
    pub exp: usize,
}

#[derive(Clone)]
pub struct OidcValidator {
    keys: Arc<RwLock<HashMap<String, DecodingKey>>>,
    jwks_url: Option<String>,
    issuer: String,
    audience: String,
    client: reqwest::Client,
}

#[derive(Deserialize)]
struct Jwks {
    keys: Vec<Jwk>,
}

#[derive(Deserialize)]
struct Jwk {
    kid: String,
    kty: String,
    n: String,
    e: String,
}

impl OidcValidator {
    pub fn from_pem(kid: &str, pem: &str, issuer: &str, audience: &str) -> Result<Self, String> {
        let key = DecodingKey::from_rsa_pem(pem.as_bytes()).map_err(|_| "invalid OIDC public key".to_owned())?;
        Ok(Self {
            keys: Arc::new(RwLock::new(HashMap::from([(kid.to_owned(), key)]))),
            jwks_url: None,
            issuer: issuer.to_owned(),
            audience: audience.to_owned(),
            client: reqwest::Client::new(),
        })
    }

    pub async fn from_jwks(jwks_url: &str, issuer: &str, audience: &str) -> Result<Self, String> {
        if !jwks_url.starts_with("https://") && !jwks_url.starts_with("http://localhost") && !jwks_url.starts_with("http://keycloak") {
            return Err("JWKS URL must use TLS outside local service networking".to_owned());
        }
        let validator = Self {
            keys: Arc::new(RwLock::new(HashMap::new())),
            jwks_url: Some(jwks_url.to_owned()),
            issuer: issuer.to_owned(),
            audience: audience.to_owned(),
            client: reqwest::Client::new(),
        };
        validator.refresh().await?;
        Ok(validator)
    }

    async fn refresh(&self) -> Result<(), String> {
        let url = self.jwks_url.as_deref().ok_or_else(|| "static validator cannot refresh keys".to_owned())?;
        let document = self.client.get(url).send().await.map_err(|_| "OIDC JWKS fetch failed".to_owned())?
            .error_for_status().map_err(|_| "OIDC JWKS endpoint rejected the request".to_owned())?
            .json::<Jwks>().await.map_err(|_| "OIDC JWKS response is invalid".to_owned())?;
        let mut refreshed = HashMap::new();
        for jwk in document.keys {
            if jwk.kty != "RSA" || jwk.kid.is_empty() {
                continue;
            }
            if let Ok(key) = DecodingKey::from_rsa_components(&jwk.n, &jwk.e) {
                refreshed.insert(jwk.kid, key);
            }
        }
        if refreshed.is_empty() {
            return Err("OIDC JWKS contains no usable RSA keys".to_owned());
        }
        *self.keys.write().await = refreshed;
        Ok(())
    }

    pub async fn validate(&self, token: &str) -> Result<Claims, String> {
        let header = decode_header(token).map_err(|_| "invalid bearer token".to_owned())?;
        let kid = header.kid.ok_or_else(|| "bearer token has no key ID".to_owned())?;
        if !self.keys.read().await.contains_key(&kid) {
            self.refresh().await?;
        }
        let key = self.keys.read().await.get(&kid).cloned().ok_or_else(|| "unknown OIDC signing key".to_owned())?;
        let mut validation = Validation::new(Algorithm::RS256);
        validation.set_issuer(&[&self.issuer]);
        validation.set_audience(&[&self.audience]);
        validation.validate_exp = true;
        let token = decode::<Claims>(token, &key, &validation).map_err(|_| "bearer token validation failed".to_owned())?;
        let claims = token.claims;
        if claims.tenant_id.is_empty() || claims.actor.is_empty() || claims.role.is_empty() {
            return Err("tenant, actor, and role claims are required".to_owned());
        }
        Ok(claims)
    }
}
