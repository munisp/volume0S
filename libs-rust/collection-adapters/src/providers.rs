use std::fmt;
use std::future::Future;
use std::pin::Pin;
use std::str::FromStr;
use std::time::Duration;

use base64::Engine;
use hmac::{Hmac, Mac};
use reqwest::{Client, Url};
use rust_decimal::prelude::ToPrimitive;
use rust_decimal::Decimal;
use serde_json::Value;
use sha2::{Sha256, Sha512};
use volumeos_tb_adapter::ledger::Currency;

const MAX_WEBHOOK_BYTES: usize = 1024 * 1024;
const MAX_PROVIDER_RESPONSE_BYTES: usize = 2 * 1024 * 1024;

pub type ProviderFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash)]
pub enum Provider {
    Paystack,
    Flutterwave,
    Moniepoint,
}

impl Provider {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Paystack => "paystack",
            Self::Flutterwave => "flutterwave",
            Self::Moniepoint => "moniepoint",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CollectionError {
    InvalidSignature,
    InvalidPayload(String),
    Provider(String),
}

impl fmt::Display for CollectionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidSignature => f.write_str("webhook signature validation failed"),
            Self::InvalidPayload(message) => write!(f, "invalid collection payload: {message}"),
            Self::Provider(message) => write!(f, "provider confirmation failed: {message}"),
        }
    }
}

impl std::error::Error for CollectionError {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PaymentNotification {
    pub provider: Provider,
    pub notification_id: String,
    pub confirmation_id: String,
    pub reference: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConfirmationStatus {
    Settled,
    Pending,
    Failed,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConfirmedPayment {
    pub provider: Provider,
    pub provider_payment_id: String,
    pub reference: String,
    pub amount_minor: u128,
    pub currency: Currency,
    pub status: ConfirmationStatus,
    pub tenant_hint: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CheckoutRequest {
    pub provider: Provider,
    pub tenant_id: String,
    pub reference: String,
    pub amount_minor: u128,
    pub currency: Currency,
    pub customer_email: String,
    pub redirect_url: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CheckoutSession {
    pub provider: Provider,
    pub reference: String,
    pub provider_session_id: String,
    pub checkout_url: String,
}

pub trait ProviderClient: Send + Sync {
    fn initialize<'a>(
        &'a self,
        request: &'a CheckoutRequest,
    ) -> ProviderFuture<'a, Result<CheckoutSession, CollectionError>>;

    fn confirm<'a>(
        &'a self,
        notification: &'a PaymentNotification,
    ) -> ProviderFuture<'a, Result<ConfirmedPayment, CollectionError>>;
}

/// Verify the raw body before parsing it. Flutterwave's current v4 Base64
/// HMAC-SHA256 scheme is required; the legacy static `verif-hash` is rejected.
pub fn verify_and_parse(
    provider: Provider,
    body: &[u8],
    signature: &str,
    secret: &str,
) -> Result<Option<PaymentNotification>, CollectionError> {
    if body.is_empty()
        || body.len() > MAX_WEBHOOK_BYTES
        || secret.is_empty()
        || signature.len() > 4096
    {
        return Err(CollectionError::InvalidSignature);
    }
    match provider {
        Provider::Paystack | Provider::Moniepoint => {
            let signature =
                hex::decode(signature.trim()).map_err(|_| CollectionError::InvalidSignature)?;
            let mut mac = <Hmac<Sha512> as Mac>::new_from_slice(secret.as_bytes())
                .map_err(|_| CollectionError::InvalidSignature)?;
            mac.update(body);
            mac.verify_slice(&signature)
                .map_err(|_| CollectionError::InvalidSignature)?;
        }
        Provider::Flutterwave => {
            let signature = base64::engine::general_purpose::STANDARD
                .decode(signature.trim())
                .map_err(|_| CollectionError::InvalidSignature)?;
            let mut mac = <Hmac<Sha256> as Mac>::new_from_slice(secret.as_bytes())
                .map_err(|_| CollectionError::InvalidSignature)?;
            mac.update(body);
            mac.verify_slice(&signature)
                .map_err(|_| CollectionError::InvalidSignature)?;
        }
    }
    let payload: Value = serde_json::from_slice(body)
        .map_err(|_| CollectionError::InvalidPayload("body is not valid JSON".to_owned()))?;
    parse_notification(provider, &payload)
}

pub fn parse_notification(
    provider: Provider,
    payload: &Value,
) -> Result<Option<PaymentNotification>, CollectionError> {
    match provider {
        Provider::Paystack => {
            if required_str(payload, "/event")? != "charge.success" {
                return Ok(None);
            }
            let reference = required_str(payload, "/data/reference")?;
            let notification_id = required_id(payload, "/data/id")?;
            Ok(Some(PaymentNotification {
                provider,
                notification_id,
                confirmation_id: reference.clone(),
                reference,
            }))
        }
        Provider::Flutterwave => {
            if required_str(payload, "/type")? != "charge.completed" {
                return Ok(None);
            }
            let notification_id = required_id(payload, "/id")?;
            let confirmation_id = required_str(payload, "/data/id")?;
            let reference = required_str(payload, "/data/reference")?;
            Ok(Some(PaymentNotification {
                provider,
                notification_id,
                confirmation_id,
                reference,
            }))
        }
        Provider::Moniepoint => {
            if let Some(event_type) = payload.get("eventType").and_then(Value::as_str) {
                if event_type != "SUCCESSFUL_TRANSACTION" {
                    return Ok(None);
                }
            }
            let confirmation_id = required_str(payload, "/transactionReference")?;
            let reference = required_str(payload, "/paymentReference")?;
            Ok(Some(PaymentNotification {
                provider,
                notification_id: confirmation_id.clone(),
                confirmation_id,
                reference,
            }))
        }
    }
}

pub struct PaystackClient {
    http: Client,
    base_url: Url,
    secret: String,
}

impl PaystackClient {
    pub fn new(value: &str, secret: String) -> Result<Self, CollectionError> {
        if secret.is_empty() {
            return Err(CollectionError::Provider(
                "Paystack API secret is not configured".to_owned(),
            ));
        }
        Ok(Self {
            http: http_client()?,
            base_url: base_url(value)?,
            secret,
        })
    }
}

impl ProviderClient for PaystackClient {
    fn initialize<'a>(
        &'a self,
        request: &'a CheckoutRequest,
    ) -> ProviderFuture<'a, Result<CheckoutSession, CollectionError>> {
        Box::pin(async move {
            validate_checkout(request, Provider::Paystack)?;
            let response = self
                .http
                .post(endpoint(&self.base_url, &["transaction", "initialize"])?)
                .bearer_auth(&self.secret)
                .json(&serde_json::json!({
                    "email": request.customer_email,
                    "amount": request.amount_minor.to_string(),
                    "currency": request.currency.as_str(),
                    "reference": request.reference,
                    "callback_url": request.redirect_url,
                    "metadata": {"tenant_id": request.tenant_id},
                }))
                .send()
                .await
                .map_err(|_| {
                    CollectionError::Provider("Paystack initialization request failed".to_owned())
                })?;
            let payload = response_json(response, "Paystack").await?;
            if payload.get("status").and_then(Value::as_bool) != Some(true) {
                return Err(CollectionError::Provider(
                    "Paystack returned an unsuccessful initialization response".to_owned(),
                ));
            }
            checkout_session(
                request,
                &payload,
                "/data/reference",
                "/data/access_code",
                "/data/authorization_url",
            )
        })
    }

    fn confirm<'a>(
        &'a self,
        notification: &'a PaymentNotification,
    ) -> ProviderFuture<'a, Result<ConfirmedPayment, CollectionError>> {
        Box::pin(async move {
            ensure_provider(notification, Provider::Paystack)?;
            let response = self
                .http
                .get(endpoint(
                    &self.base_url,
                    &["transaction", "verify", &notification.confirmation_id],
                )?)
                .bearer_auth(&self.secret)
                .send()
                .await
                .map_err(|_| {
                    CollectionError::Provider("Paystack verification request failed".to_owned())
                })?;
            let payload = response_json(response, "Paystack").await?;
            if payload.get("status").and_then(Value::as_bool) != Some(true) {
                return Err(CollectionError::Provider(
                    "Paystack returned an unsuccessful verification response".to_owned(),
                ));
            }
            let reference = required_str(&payload, "/data/reference")?;
            if reference != notification.reference {
                return Err(CollectionError::Provider(
                    "Paystack reference changed during confirmation".to_owned(),
                ));
            }
            let status = match required_str(&payload, "/data/status")?.as_str() {
                "success" => ConfirmationStatus::Settled,
                "pending" | "ongoing" => ConfirmationStatus::Pending,
                _ => ConfirmationStatus::Failed,
            };
            Ok(ConfirmedPayment {
                provider: Provider::Paystack,
                provider_payment_id: required_id(&payload, "/data/id")?,
                reference,
                amount_minor: required_minor_integer(&payload, "/data/amount")?,
                currency: required_currency(&payload, "/data/currency")?,
                status,
                tenant_hint: optional_string(&payload, "/data/metadata/tenant_id"),
            })
        })
    }
}

pub struct FlutterwaveClient {
    http: Client,
    api_base: Url,
    token_url: Url,
    client_id: String,
    client_secret: String,
}

impl FlutterwaveClient {
    pub fn new(
        api_base: &str,
        token_url: &str,
        client_id: String,
        client_secret: String,
    ) -> Result<Self, CollectionError> {
        if client_id.is_empty() || client_secret.is_empty() {
            return Err(CollectionError::Provider(
                "Flutterwave OAuth credentials are not configured".to_owned(),
            ));
        }
        Ok(Self {
            http: http_client()?,
            api_base: base_url(api_base)?,
            token_url: secure_url(token_url)?,
            client_id,
            client_secret,
        })
    }

    async fn access_token(&self) -> Result<String, CollectionError> {
        let response = self
            .http
            .post(self.token_url.clone())
            .form(&[
                ("client_id", self.client_id.as_str()),
                ("client_secret", self.client_secret.as_str()),
                ("grant_type", "client_credentials"),
            ])
            .send()
            .await
            .map_err(|_| {
                CollectionError::Provider("Flutterwave OAuth request failed".to_owned())
            })?;
        let payload = response_json(response, "Flutterwave OAuth").await?;
        required_str(&payload, "/access_token")
    }
}

impl ProviderClient for FlutterwaveClient {
    fn initialize<'a>(
        &'a self,
        request: &'a CheckoutRequest,
    ) -> ProviderFuture<'a, Result<CheckoutSession, CollectionError>> {
        Box::pin(async move {
            validate_checkout(request, Provider::Flutterwave)?;
            let token = self.access_token().await?;
            let trace_id = blake3::hash(request.reference.as_bytes())
                .to_hex()
                .to_string();
            let idempotency_key =
                blake3::hash(format!("{}\x1f{}", request.tenant_id, request.reference).as_bytes())
                    .to_hex()
                    .to_string();
            let customer_response = self
                .http
                .post(endpoint(&self.api_base, &["customers"])?)
                .bearer_auth(&token)
                .header("X-Trace-Id", &trace_id)
                .header("X-Idempotency-Key", format!("{idempotency_key}:customer"))
                .json(&serde_json::json!({
                    "email": request.customer_email,
                    "meta": {"tenant_id": request.tenant_id},
                }))
                .send()
                .await
                .map_err(|_| {
                    CollectionError::Provider("Flutterwave customer creation failed".to_owned())
                })?;
            let customer = response_json(customer_response, "Flutterwave").await?;
            let customer_id = required_str(&customer, "/data/id")?;
            let method_response = self
                .http
                .post(endpoint(&self.api_base, &["payment-methods"])?)
                .bearer_auth(&token)
                .header("X-Trace-Id", &trace_id)
                .header("X-Idempotency-Key", format!("{idempotency_key}:method"))
                .json(&serde_json::json!({"type": "opay"}))
                .send()
                .await
                .map_err(|_| {
                    CollectionError::Provider(
                        "Flutterwave payment method creation failed".to_owned(),
                    )
                })?;
            let method = response_json(method_response, "Flutterwave").await?;
            let payment_method_id = required_str(&method, "/data/id")?;
            let charge_response = self
                .http
                .post(endpoint(&self.api_base, &["charges"])?)
                .bearer_auth(token)
                .header("X-Trace-Id", trace_id)
                .header("X-Idempotency-Key", format!("{idempotency_key}:charge"))
                .json(&serde_json::json!({
                    "reference": request.reference,
                    "currency": request.currency.as_str(),
                    "customer_id": customer_id,
                    "payment_method_id": payment_method_id,
                    "redirect_url": request.redirect_url,
                    "amount": major_units(request.amount_minor),
                    "meta": {"tenant_id": request.tenant_id},
                }))
                .send()
                .await
                .map_err(|_| {
                    CollectionError::Provider("Flutterwave charge initialization failed".to_owned())
                })?;
            let charge = response_json(charge_response, "Flutterwave").await?;
            checkout_session(
                request,
                &charge,
                "/data/reference",
                "/data/id",
                "/data/next_action/redirect_url/url",
            )
        })
    }

    fn confirm<'a>(
        &'a self,
        notification: &'a PaymentNotification,
    ) -> ProviderFuture<'a, Result<ConfirmedPayment, CollectionError>> {
        Box::pin(async move {
            ensure_provider(notification, Provider::Flutterwave)?;
            let token = self.access_token().await?;
            let trace_id = blake3::hash(notification.notification_id.as_bytes())
                .to_hex()
                .to_string();
            let response = self
                .http
                .get(endpoint(
                    &self.api_base,
                    &["charges", &notification.confirmation_id],
                )?)
                .bearer_auth(token)
                .header("X-Trace-Id", trace_id)
                .send()
                .await
                .map_err(|_| {
                    CollectionError::Provider("Flutterwave charge retrieval failed".to_owned())
                })?;
            let payload = response_json(response, "Flutterwave").await?;
            if required_str(&payload, "/status")? != "success" {
                return Err(CollectionError::Provider(
                    "Flutterwave returned an unsuccessful verification response".to_owned(),
                ));
            }
            if required_str(&payload, "/data/id")? != notification.confirmation_id {
                return Err(CollectionError::Provider(
                    "Flutterwave charge ID changed during confirmation".to_owned(),
                ));
            }
            let reference = required_str(&payload, "/data/reference")?;
            if reference != notification.reference {
                return Err(CollectionError::Provider(
                    "Flutterwave reference changed during confirmation".to_owned(),
                ));
            }
            let status = match required_str(&payload, "/data/status")?.as_str() {
                "succeeded" => ConfirmationStatus::Settled,
                "pending" => ConfirmationStatus::Pending,
                _ => ConfirmationStatus::Failed,
            };
            Ok(ConfirmedPayment {
                provider: Provider::Flutterwave,
                provider_payment_id: notification.confirmation_id.clone(),
                reference,
                amount_minor: required_decimal_minor(&payload, "/data/amount")?,
                currency: required_currency(&payload, "/data/currency")?,
                status,
                tenant_hint: optional_string(&payload, "/data/meta/tenant_id"),
            })
        })
    }
}

pub struct MoniepointClient {
    http: Client,
    api_base: Url,
    client_key: String,
    client_secret: String,
    contract_code: String,
}

impl MoniepointClient {
    pub fn new(
        api_base: &str,
        client_key: String,
        client_secret: String,
        contract_code: String,
    ) -> Result<Self, CollectionError> {
        if client_key.is_empty() || client_secret.is_empty() || contract_code.is_empty() {
            return Err(CollectionError::Provider(
                "Moniepoint API credentials and contract code are not configured".to_owned(),
            ));
        }
        Ok(Self {
            http: http_client()?,
            api_base: base_url(api_base)?,
            client_key,
            client_secret,
            contract_code,
        })
    }

    async fn access_token(&self) -> Result<String, CollectionError> {
        let response = self
            .http
            .post(endpoint(&self.api_base, &["api", "v1", "auth", "login"])?)
            .basic_auth(&self.client_key, Some(&self.client_secret))
            .send()
            .await
            .map_err(|_| {
                CollectionError::Provider("Moniepoint authentication request failed".to_owned())
            })?;
        let payload = response_json(response, "Moniepoint authentication").await?;
        required_str(&payload, "/responseBody/accessToken")
    }
}

impl ProviderClient for MoniepointClient {
    fn initialize<'a>(
        &'a self,
        request: &'a CheckoutRequest,
    ) -> ProviderFuture<'a, Result<CheckoutSession, CollectionError>> {
        Box::pin(async move {
            validate_checkout(request, Provider::Moniepoint)?;
            let token = self.access_token().await?;
            let response = self
                .http
                .post(endpoint(
                    &self.api_base,
                    &["api", "v1", "merchant", "transactions", "init-transaction"],
                )?)
                .bearer_auth(token)
                .json(&serde_json::json!({
                    "amount": major_units(request.amount_minor),
                    "customerName": "VolumeOS tenant",
                    "customerEmail": request.customer_email,
                    "paymentReference": request.reference,
                    "paymentDescription": "VolumeOS prepaid wallet top-up",
                    "currencyCode": request.currency.as_str(),
                    "contractCode": self.contract_code,
                    "redirectUrl": request.redirect_url,
                    "metaData": {"tenant_id": request.tenant_id},
                }))
                .send()
                .await
                .map_err(|_| {
                    CollectionError::Provider("Moniepoint initialization request failed".to_owned())
                })?;
            let payload = response_json(response, "Moniepoint").await?;
            if payload.get("requestSuccessful").and_then(Value::as_bool) != Some(true) {
                return Err(CollectionError::Provider(
                    "Moniepoint returned an unsuccessful initialization response".to_owned(),
                ));
            }
            checkout_session(
                request,
                &payload,
                "/responseBody/paymentReference",
                "/responseBody/transactionReference",
                "/responseBody/checkoutUrl",
            )
        })
    }

    fn confirm<'a>(
        &'a self,
        notification: &'a PaymentNotification,
    ) -> ProviderFuture<'a, Result<ConfirmedPayment, CollectionError>> {
        Box::pin(async move {
            ensure_provider(notification, Provider::Moniepoint)?;
            let token = self.access_token().await?;
            let response = self
                .http
                .get(endpoint(
                    &self.api_base,
                    &["api", "v2", "transactions", &notification.confirmation_id],
                )?)
                .bearer_auth(token)
                .send()
                .await
                .map_err(|_| {
                    CollectionError::Provider("Moniepoint transaction retrieval failed".to_owned())
                })?;
            let payload = response_json(response, "Moniepoint").await?;
            if payload.get("requestSuccessful").and_then(Value::as_bool) != Some(true) {
                return Err(CollectionError::Provider(
                    "Moniepoint returned an unsuccessful verification response".to_owned(),
                ));
            }
            if required_str(&payload, "/responseBody/transactionReference")?
                != notification.confirmation_id
            {
                return Err(CollectionError::Provider(
                    "Moniepoint transaction reference changed during confirmation".to_owned(),
                ));
            }
            let reference = required_str(&payload, "/responseBody/paymentReference")?;
            if reference != notification.reference {
                return Err(CollectionError::Provider(
                    "Moniepoint payment reference changed during confirmation".to_owned(),
                ));
            }
            let status = match required_str(&payload, "/responseBody/paymentStatus")?.as_str() {
                "PAID" => ConfirmationStatus::Settled,
                "PENDING" | "PARTIALLY_PAID" => ConfirmationStatus::Pending,
                _ => ConfirmationStatus::Failed,
            };
            Ok(ConfirmedPayment {
                provider: Provider::Moniepoint,
                provider_payment_id: notification.confirmation_id.clone(),
                reference,
                amount_minor: required_decimal_minor(&payload, "/responseBody/amountPaid")?,
                currency: required_currency(&payload, "/responseBody/currency")?,
                status,
                tenant_hint: optional_string(&payload, "/responseBody/metaData/tenant_id"),
            })
        })
    }
}

fn validate_checkout(request: &CheckoutRequest, provider: Provider) -> Result<(), CollectionError> {
    let valid_email = request.customer_email.len() <= 320
        && !request.customer_email.chars().any(char::is_whitespace)
        && request.customer_email.matches('@').count() == 1;
    if request.provider != provider
        || request.amount_minor == 0
        || request.tenant_id.is_empty()
        || request.tenant_id.len() > 128
        || request.reference.is_empty()
        || request.reference.len() > 128
        || !valid_email
    {
        return Err(CollectionError::InvalidPayload(
            "checkout scope, reference, amount, or email is invalid".to_owned(),
        ));
    }
    secure_url(&request.redirect_url)?;
    Ok(())
}

fn checkout_session(
    request: &CheckoutRequest,
    payload: &Value,
    reference_pointer: &str,
    session_pointer: &str,
    url_pointer: &str,
) -> Result<CheckoutSession, CollectionError> {
    let reference = required_str(payload, reference_pointer)?;
    if reference != request.reference {
        return Err(CollectionError::Provider(
            "provider changed the checkout reference".to_owned(),
        ));
    }
    let checkout_url = required_str(payload, url_pointer)?;
    secure_url(&checkout_url)?;
    Ok(CheckoutSession {
        provider: request.provider,
        reference,
        provider_session_id: required_str(payload, session_pointer)?,
        checkout_url,
    })
}

fn major_units(amount_minor: u128) -> String {
    (Decimal::from(amount_minor) / Decimal::from(100)).to_string()
}

fn ensure_provider(
    notification: &PaymentNotification,
    provider: Provider,
) -> Result<(), CollectionError> {
    if notification.provider == provider {
        Ok(())
    } else {
        Err(CollectionError::InvalidPayload(
            "provider client and notification differ".to_owned(),
        ))
    }
}

fn http_client() -> Result<Client, CollectionError> {
    Client::builder()
        .timeout(Duration::from_secs(20))
        .user_agent("volumeos-collections/0.1")
        .build()
        .map_err(|_| {
            CollectionError::Provider("failed to initialize provider HTTP client".to_owned())
        })
}

fn secure_url(value: &str) -> Result<Url, CollectionError> {
    let url = Url::parse(value)
        .map_err(|_| CollectionError::Provider("provider URL is invalid".to_owned()))?;
    let loopback = matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "::1"));
    if url.scheme() != "https" && !(url.scheme() == "http" && loopback) {
        return Err(CollectionError::Provider(
            "provider URL must use HTTPS".to_owned(),
        ));
    }
    Ok(url)
}

fn base_url(value: &str) -> Result<Url, CollectionError> {
    let mut url = secure_url(value)?;
    url.set_query(None);
    url.set_fragment(None);
    Ok(url)
}

fn endpoint(base: &Url, segments: &[&str]) -> Result<Url, CollectionError> {
    let mut url = base.clone();
    {
        let mut path = url.path_segments_mut().map_err(|_| {
            CollectionError::Provider("provider base URL cannot contain path segments".to_owned())
        })?;
        path.pop_if_empty();
        for segment in segments {
            if segment.is_empty() {
                return Err(CollectionError::InvalidPayload(
                    "provider reference is empty".to_owned(),
                ));
            }
            path.push(segment);
        }
    }
    Ok(url)
}

async fn response_json(
    mut response: reqwest::Response,
    provider: &str,
) -> Result<Value, CollectionError> {
    if !response.status().is_success() {
        return Err(CollectionError::Provider(format!(
            "{provider} returned HTTP {}",
            response.status().as_u16()
        )));
    }
    if response
        .content_length()
        .is_some_and(|length| length > MAX_PROVIDER_RESPONSE_BYTES as u64)
    {
        return Err(CollectionError::Provider(format!(
            "{provider} response exceeded the size limit"
        )));
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| CollectionError::Provider(format!("{provider} response could not be read")))?
    {
        if bytes.len().saturating_add(chunk.len()) > MAX_PROVIDER_RESPONSE_BYTES {
            return Err(CollectionError::Provider(format!(
                "{provider} response exceeded the size limit"
            )));
        }
        bytes.extend_from_slice(&chunk);
    }
    serde_json::from_slice(&bytes)
        .map_err(|_| CollectionError::Provider(format!("{provider} returned invalid JSON")))
}

fn required_str(payload: &Value, pointer: &str) -> Result<String, CollectionError> {
    let value = payload
        .pointer(pointer)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty() && value.len() <= 512)
        .ok_or_else(|| {
            CollectionError::InvalidPayload(format!("missing or invalid string at {pointer}"))
        })?;
    Ok(value.to_owned())
}

fn required_id(payload: &Value, pointer: &str) -> Result<String, CollectionError> {
    let value = payload
        .pointer(pointer)
        .ok_or_else(|| CollectionError::InvalidPayload(format!("missing ID at {pointer}")))?;
    match value {
        Value::String(_) => required_str(payload, pointer),
        Value::Number(number) => Ok(number.to_string()),
        _ => Err(CollectionError::InvalidPayload(format!(
            "invalid ID at {pointer}"
        ))),
    }
}

fn optional_string(payload: &Value, pointer: &str) -> Option<String> {
    payload
        .pointer(pointer)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty() && value.len() <= 128)
        .map(str::to_owned)
}

fn required_currency(payload: &Value, pointer: &str) -> Result<Currency, CollectionError> {
    match required_str(payload, pointer)?.as_str() {
        "NGN" => Ok(Currency::Ngn),
        "USD" => Ok(Currency::Usd),
        _ => Err(CollectionError::InvalidPayload(format!(
            "unsupported currency at {pointer}"
        ))),
    }
}

fn required_minor_integer(payload: &Value, pointer: &str) -> Result<u128, CollectionError> {
    let value = payload
        .pointer(pointer)
        .ok_or_else(|| CollectionError::InvalidPayload(format!("missing amount at {pointer}")))?;
    let text = match value {
        Value::Number(number) => number.to_string(),
        Value::String(text) => text.clone(),
        _ => {
            return Err(CollectionError::InvalidPayload(format!(
                "invalid amount at {pointer}"
            )))
        }
    };
    let amount = text.parse::<u128>().map_err(|_| {
        CollectionError::InvalidPayload(format!("invalid minor-unit amount at {pointer}"))
    })?;
    if amount == 0 {
        return Err(CollectionError::InvalidPayload(
            "payment amount must be non-zero".to_owned(),
        ));
    }
    Ok(amount)
}

fn required_decimal_minor(payload: &Value, pointer: &str) -> Result<u128, CollectionError> {
    let value = payload
        .pointer(pointer)
        .ok_or_else(|| CollectionError::InvalidPayload(format!("missing amount at {pointer}")))?;
    let text = match value {
        Value::Number(number) => number.to_string(),
        Value::String(text) => text.clone(),
        _ => {
            return Err(CollectionError::InvalidPayload(format!(
                "invalid amount at {pointer}"
            )))
        }
    };
    let major = Decimal::from_str(&text).map_err(|_| {
        CollectionError::InvalidPayload(format!("invalid decimal amount at {pointer}"))
    })?;
    let minor = major * Decimal::from(100);
    if minor.fract() != Decimal::from(0) || minor.is_sign_negative() {
        return Err(CollectionError::InvalidPayload(format!(
            "amount at {pointer} has sub-minor or negative units"
        )));
    }
    let amount = minor.to_u128().ok_or_else(|| {
        CollectionError::InvalidPayload(format!("amount at {pointer} is out of range"))
    })?;
    if amount == 0 {
        return Err(CollectionError::InvalidPayload(
            "payment amount must be non-zero".to_owned(),
        ));
    }
    Ok(amount)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn sha512_signature(body: &[u8], secret: &str) -> String {
        let mut mac = <Hmac<Sha512> as Mac>::new_from_slice(secret.as_bytes()).unwrap();
        mac.update(body);
        hex::encode(mac.finalize().into_bytes())
    }

    fn sha256_signature(body: &[u8], secret: &str) -> String {
        let mut mac = <Hmac<Sha256> as Mac>::new_from_slice(secret.as_bytes()).unwrap();
        mac.update(body);
        base64::engine::general_purpose::STANDARD.encode(mac.finalize().into_bytes())
    }

    #[test]
    fn verifies_and_parses_all_supported_webhook_schemes() {
        let secret = "test-only-secret";
        let paystack = serde_json::to_vec(&json!({
            "event": "charge.success",
            "data": {"id": 12345, "reference": "order-1"}
        }))
        .unwrap();
        let parsed = verify_and_parse(
            Provider::Paystack,
            &paystack,
            &sha512_signature(&paystack, secret),
            secret,
        )
        .unwrap()
        .unwrap();
        assert_eq!(parsed.provider, Provider::Paystack);
        assert_eq!(parsed.notification_id, "12345");
        assert_eq!(parsed.confirmation_id, "order-1");

        let flutterwave = serde_json::to_vec(&json!({
            "id": "event-1",
            "type": "charge.completed",
            "data": {"id": "charge-1", "reference": "order-1"}
        }))
        .unwrap();
        let parsed = verify_and_parse(
            Provider::Flutterwave,
            &flutterwave,
            &sha256_signature(&flutterwave, secret),
            secret,
        )
        .unwrap()
        .unwrap();
        assert_eq!(parsed.provider, Provider::Flutterwave);
        assert_eq!(parsed.notification_id, "event-1");
        assert_eq!(parsed.confirmation_id, "charge-1");

        let moniepoint = serde_json::to_vec(&json!({
            "eventType": "SUCCESSFUL_TRANSACTION",
            "transactionReference": "transaction-1",
            "paymentReference": "order-1"
        }))
        .unwrap();
        let parsed = verify_and_parse(
            Provider::Moniepoint,
            &moniepoint,
            &sha512_signature(&moniepoint, secret),
            secret,
        )
        .unwrap()
        .unwrap();
        assert_eq!(parsed.provider, Provider::Moniepoint);
        assert_eq!(parsed.confirmation_id, "transaction-1");
        assert_eq!(parsed.reference, "order-1");
    }

    #[test]
    fn rejects_tampering_and_ignores_authenticated_unsupported_events() {
        let secret = "test-only-secret";
        let body = br#"{"event":"charge.success","data":{"id":1,"reference":"order-1"}}"#;
        let signature = sha512_signature(body, secret);
        let mut tampered = body.to_vec();
        tampered.pop();
        tampered.push(b' ');
        assert_eq!(
            verify_and_parse(Provider::Paystack, &tampered, &signature, secret),
            Err(CollectionError::InvalidSignature)
        );

        let ignored = br#"{"event":"charge.failed","data":{"id":1,"reference":"order-1"}}"#;
        assert!(verify_and_parse(
            Provider::Paystack,
            ignored,
            &sha512_signature(ignored, secret),
            secret,
        )
        .unwrap()
        .is_none());
    }

    #[test]
    fn amount_parsing_is_exact_and_rejects_invalid_values() {
        assert_eq!(
            required_minor_integer(&json!({"amount": 125000}), "/amount").unwrap(),
            125000
        );
        assert_eq!(
            required_minor_integer(&json!({"amount": "125000"}), "/amount").unwrap(),
            125000
        );
        for value in [json!(0), json!(12.5), json!(-1), json!("1.5")] {
            assert!(required_minor_integer(&json!({"amount": value}), "/amount").is_err());
        }

        assert_eq!(
            required_decimal_minor(&json!({"amount": "1250.00"}), "/amount").unwrap(),
            125000
        );
        assert_eq!(
            required_decimal_minor(&json!({"amount": 1250}), "/amount").unwrap(),
            125000
        );
        for value in [json!(0), json!("1250.001"), json!(-1), json!("not-money")] {
            assert!(required_decimal_minor(&json!({"amount": value}), "/amount").is_err());
        }
    }

    #[test]
    fn provider_configuration_fails_closed_without_exposing_secrets() {
        assert!(PaystackClient::new("https://api.paystack.co", String::new()).is_err());
        assert!(PaystackClient::new("http://api.paystack.co", "secret".to_owned()).is_err());
        assert!(PaystackClient::new("http://127.0.0.1:8080", "secret".to_owned()).is_ok());
        assert!(FlutterwaveClient::new(
            "https://api.flutterwave.cloud",
            "https://idp.flutterwave.com/realms/flutterwave/protocol/openid-connect/token",
            String::new(),
            "secret".to_owned(),
        )
        .is_err());
        assert!(MoniepointClient::new(
            "https://api.monnify.com",
            "key".to_owned(),
            String::new(),
            "contract".to_owned(),
        )
        .is_err());
    }
}
