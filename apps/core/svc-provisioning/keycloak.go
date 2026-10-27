package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"
)

type KeycloakClient struct {
	adminURL     string
	tokenURL     string
	clientID     string
	clientSecret string
	httpClient   *http.Client

	mu          sync.Mutex
	accessToken string
	tokenExpiry time.Time
}

func NewKeycloakClientFromEnv() (*KeycloakClient, error) {
	client := &KeycloakClient{
		adminURL: strings.TrimRight(os.Getenv("KEYCLOAK_ADMIN_URL"), "/"),
		tokenURL: os.Getenv("KEYCLOAK_ADMIN_TOKEN_URL"),
		clientID: os.Getenv("KEYCLOAK_ADMIN_CLIENT_ID"), clientSecret: os.Getenv("KEYCLOAK_ADMIN_CLIENT_SECRET"),
		httpClient: &http.Client{Timeout: 15 * time.Second},
	}
	if !validURL(client.adminURL) || !validURL(client.tokenURL) || client.clientID == "" || len(client.clientSecret) < 16 {
		return nil, errors.New("Keycloak admin URL, token URL, client ID, and a strong client secret are required")
	}
	return client, nil
}

func validURL(value string) bool {
	parsed, err := url.Parse(value)
	return err == nil && (parsed.Scheme == "https" || parsed.Scheme == "http") && parsed.Host != ""
}

func (k *KeycloakClient) token(ctx context.Context) (string, error) {
	k.mu.Lock()
	defer k.mu.Unlock()
	if k.accessToken != "" && time.Now().Before(k.tokenExpiry) {
		return k.accessToken, nil
	}
	form := url.Values{
		"grant_type": {"client_credentials"}, "client_id": {k.clientID}, "client_secret": {k.clientSecret},
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, k.tokenURL, strings.NewReader(form.Encode()))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	response, err := k.httpClient.Do(req)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("Keycloak token endpoint returned %d", response.StatusCode)
	}
	var body struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&body); err != nil || body.AccessToken == "" {
		return "", errors.New("Keycloak token endpoint returned an invalid response")
	}
	k.accessToken = body.AccessToken
	k.tokenExpiry = time.Now().Add(time.Duration(max(body.ExpiresIn-30, 1)) * time.Second)
	return k.accessToken, nil
}

func (k *KeycloakClient) request(ctx context.Context, method, path string, query url.Values, payload any) ([]byte, int, error) {
	token, err := k.token(ctx)
	if err != nil {
		return nil, 0, err
	}
	var body io.Reader
	if payload != nil {
		encoded, err := json.Marshal(payload)
		if err != nil {
			return nil, 0, err
		}
		body = bytes.NewReader(encoded)
	}
	endpoint := k.adminURL + path
	if len(query) > 0 {
		endpoint += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, method, endpoint, body)
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	response, err := k.httpClient.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer response.Body.Close()
	content, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return nil, 0, err
	}
	return content, response.StatusCode, nil
}

type keycloakClientRepresentation struct {
	ID       string `json:"id"`
	ClientID string `json:"clientId"`
}

func tenantClientID(tenantID string) string { return "volumeos-" + tenantID }

func (k *KeycloakClient) find(ctx context.Context, tenantID string) (*keycloakClientRepresentation, error) {
	content, status, err := k.request(ctx, http.MethodGet, "/clients", url.Values{"clientId": {tenantClientID(tenantID)}}, nil)
	if err != nil {
		return nil, err
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("Keycloak client lookup returned %d", status)
	}
	var clients []keycloakClientRepresentation
	if err := json.Unmarshal(content, &clients); err != nil {
		return nil, errors.New("Keycloak client lookup returned invalid JSON")
	}
	for _, client := range clients {
		if client.ClientID == tenantClientID(tenantID) && client.ID != "" {
			return &client, nil
		}
	}
	return nil, nil
}

func (k *KeycloakClient) EnsureTenant(ctx context.Context, tenantID, name, corridor string) (string, bool, error) {
	existing, err := k.find(ctx, tenantID)
	if err != nil || existing != nil {
		if existing != nil {
			// Reused clients are not owned by this onboarding attempt and must
			// survive any later saga compensation.
			return existing.ClientID, false, nil
		}
		return "", false, err
	}
	payload := map[string]any{
		"clientId": tenantClientID(tenantID), "name": name, "description": "VolumeOS tenant " + tenantID,
		"protocol": "openid-connect", "enabled": true, "publicClient": false,
		"standardFlowEnabled": false, "directAccessGrantsEnabled": false, "serviceAccountsEnabled": true,
		"attributes": map[string]string{"tenant_id": tenantID, "corridor": corridor},
		"protocolMappers": []map[string]any{{
			"name": "volumeos-tenant-id", "protocol": "openid-connect", "protocolMapper": "oidc-hardcoded-claim-mapper",
			"config": map[string]string{
				"claim.name": "tenant_id", "claim.value": tenantID, "jsonType.label": "String",
				"access.token.claim": "true", "id.token.claim": "true", "userinfo.token.claim": "true",
			},
		}},
	}
	_, status, err := k.request(ctx, http.MethodPost, "/clients", nil, payload)
	if err != nil {
		return "", false, err
	}
	if status != http.StatusCreated && status != http.StatusConflict {
		return "", false, fmt.Errorf("Keycloak client creation returned %d", status)
	}
	created, err := k.find(ctx, tenantID)
	if err != nil || created == nil {
		return "", false, errors.New("Keycloak client creation could not be confirmed")
	}
	return created.ClientID, status == http.StatusCreated, nil
}

func (k *KeycloakClient) DeleteTenant(ctx context.Context, tenantID string) error {
	existing, err := k.find(ctx, tenantID)
	if err != nil || existing == nil {
		return err
	}
	_, status, err := k.request(ctx, http.MethodDelete, "/clients/"+url.PathEscape(existing.ID), nil, nil)
	if err != nil {
		return err
	}
	if status != http.StatusNoContent && status != http.StatusNotFound {
		return fmt.Errorf("Keycloak client deletion returned %d", status)
	}
	return nil
}
