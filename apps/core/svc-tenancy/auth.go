package main

import (
	"context"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

type contextKey string

const claimsKey contextKey = "claims"

type Claims struct {
	TenantID string
	Roles    map[string]bool
}

func (c Claims) Can(role string) bool {
	if c.Roles["super-operator"] || c.Roles[role] {
		return true
	}
	if role == "reader" {
		for _, allowed := range []string{"delivery", "sales", "analyst", "client-owner", "partner", "auditor"} {
			if c.Roles[allowed] {
				return true
			}
		}
	}
	return false
}

type Authenticator struct {
	issuer   string
	audience string
	jwksURL  string
	client   *http.Client
	insecure bool

	mu   sync.RWMutex
	keys map[string]*rsa.PublicKey
}

func NewAuthenticator() (*Authenticator, error) {
	insecure := os.Getenv("ALLOW_INSECURE_HEADER_AUTH") == "true"
	if insecure && os.Getenv("VO_ENV") == "prod" {
		return nil, errors.New("header authentication cannot be enabled in prod")
	}
	return &Authenticator{
		issuer: os.Getenv("OIDC_ISSUER"), audience: os.Getenv("OIDC_AUDIENCE"),
		jwksURL: os.Getenv("OIDC_JWKS_URL"), client: &http.Client{Timeout: 5 * time.Second},
		insecure: insecure, keys: make(map[string]*rsa.PublicKey),
	}, nil
}

func (a *Authenticator) middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		claims, err := a.authenticate(r)
		if err != nil {
			writeError(w, http.StatusUnauthorized, "unauthorized", "valid OIDC credentials and tenant scope are required")
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), claimsKey, claims)))
	})
}

func (a *Authenticator) authenticate(r *http.Request) (Claims, error) {
	if a.insecure {
		tenantID := r.Header.Get("X-VolumeOS-Tenant")
		roles := make(map[string]bool)
		for _, role := range strings.Split(r.Header.Get("X-VolumeOS-Roles"), ",") {
			roles[strings.TrimSpace(role)] = true
		}
		return validateClaims(Claims{TenantID: tenantID, Roles: roles})
	}
	if a.issuer == "" || a.audience == "" || a.jwksURL == "" {
		return Claims{}, errors.New("OIDC is not configured")
	}
	header := r.Header.Get("Authorization")
	if !strings.HasPrefix(header, "Bearer ") {
		return Claims{}, errors.New("missing bearer token")
	}
	token, err := jwt.ParseWithClaims(strings.TrimPrefix(header, "Bearer "), &rawClaims{}, func(token *jwt.Token) (any, error) {
		if token.Method.Alg() != jwt.SigningMethodRS256.Alg() {
			return nil, errors.New("unexpected signing method")
		}
		kid, _ := token.Header["kid"].(string)
		return a.key(r.Context(), kid)
	}, jwt.WithIssuer(a.issuer), jwt.WithAudience(a.audience), jwt.WithExpirationRequired())
	if err != nil {
		return Claims{}, err
	}
	claims, ok := token.Claims.(*rawClaims)
	if !ok {
		return Claims{}, errors.New("invalid claims")
	}
	return validateClaims(Claims{TenantID: claims.TenantID, Roles: claims.Roles})
}

type rawClaims struct {
	jwt.RegisteredClaims
	TenantID string          `json:"tenant_id"`
	Roles    map[string]bool `json:"-"`
}

func (c *rawClaims) UnmarshalJSON(data []byte) error {
	type alias rawClaims
	var decoded struct {
		alias
		TenantID    string   `json:"tenant_id"`
		Roles       []string `json:"roles"`
		RealmAccess struct {
			Roles []string `json:"roles"`
		} `json:"realm_access"`
	}
	if err := json.Unmarshal(data, &decoded); err != nil {
		return err
	}
	c.RegisteredClaims = decoded.RegisteredClaims
	c.TenantID = decoded.TenantID
	c.Roles = make(map[string]bool)
	for _, role := range append(decoded.Roles, decoded.RealmAccess.Roles...) {
		c.Roles[role] = true
	}
	return nil
}

func validateClaims(c Claims) (Claims, error) {
	delete(c.Roles, "")
	if !tenantIDPattern.MatchString(c.TenantID) || len(c.Roles) == 0 {
		return Claims{}, errors.New("tenant_id and roles claims are required")
	}
	return c, nil
}

func claimsFrom(ctx context.Context) Claims { return ctx.Value(claimsKey).(Claims) }

func (a *Authenticator) key(ctx context.Context, kid string) (*rsa.PublicKey, error) {
	a.mu.RLock()
	key := a.keys[kid]
	a.mu.RUnlock()
	if key != nil {
		return key, nil
	}
	if err := a.refreshKeys(ctx); err != nil {
		return nil, err
	}
	a.mu.RLock()
	defer a.mu.RUnlock()
	if key = a.keys[kid]; key == nil {
		return nil, errors.New("unknown signing key")
	}
	return key, nil
}

func (a *Authenticator) refreshKeys(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, a.jwksURL, nil)
	if err != nil {
		return err
	}
	resp, err := a.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("JWKS returned %s", resp.Status)
	}
	var document struct {
		Keys []struct {
			Kid string `json:"kid"`
			Kty string `json:"kty"`
			N   string `json:"n"`
			E   string `json:"e"`
		} `json:"keys"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&document); err != nil {
		return err
	}
	keys := make(map[string]*rsa.PublicKey)
	for _, item := range document.Keys {
		if item.Kty != "RSA" || item.Kid == "" {
			continue
		}
		nBytes, err1 := base64.RawURLEncoding.DecodeString(item.N)
		eBytes, err2 := base64.RawURLEncoding.DecodeString(item.E)
		if err1 != nil || err2 != nil || len(eBytes) > 8 {
			continue
		}
		e := 0
		for _, b := range eBytes {
			e = e<<8 | int(b)
		}
		keys[item.Kid] = &rsa.PublicKey{N: new(big.Int).SetBytes(nBytes), E: e}
	}
	a.mu.Lock()
	a.keys = keys
	a.mu.Unlock()
	return nil
}
