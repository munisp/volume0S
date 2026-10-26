package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

var (
	ErrIntegrationNotFound = errors.New("Postiz integration is not connected for this tenant")
	ErrIntegrationDisabled = errors.New("Postiz integration is disabled")
)

type PostizClient struct {
	baseURL     string
	daprURL     string
	secretStore string
	apiToken    string
	httpClient  *http.Client
}

func NewPostizClientFromEnv() (*PostizClient, error) {
	client := &PostizClient{
		baseURL:     strings.TrimRight(os.Getenv("POSTIZ_URL"), "/"),
		daprURL:     strings.TrimRight(os.Getenv("DAPR_HTTP_URL"), "/"),
		secretStore: os.Getenv("POSTIZ_SECRET_STORE"), apiToken: os.Getenv("DAPR_API_TOKEN"),
		httpClient:  &http.Client{Timeout: 15 * time.Second},
	}
	if !validURL(client.baseURL) || !validURL(client.daprURL) || client.secretStore == "" {
		return nil, errors.New("Postiz URL, Dapr HTTP URL, and secret store are required")
	}
	return client, nil
}

func (p *PostizClient) tenantAPIKey(ctx context.Context, tenantID string) (string, error) {
	secretName := "postiz-" + tenantID + "-api-key"
	endpoint := p.daprURL + "/v1.0/secrets/" + url.PathEscape(p.secretStore) + "/" + url.PathEscape(secretName)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return "", err
	}
	if p.apiToken != "" {
		req.Header.Set("dapr-api-token", p.apiToken)
	}
	response, err := p.httpClient.Do(req)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("tenant Postiz credential lookup returned %d", response.StatusCode)
	}
	var secrets map[string]string
	if err := json.NewDecoder(io.LimitReader(response.Body, 64*1024)).Decode(&secrets); err != nil {
		return "", errors.New("tenant Postiz credential lookup returned invalid JSON")
	}
	if secrets[secretName] == "" {
		return "", errors.New("tenant Postiz credential is empty")
	}
	return secrets[secretName], nil
}

func (p *PostizClient) ValidateIntegration(ctx context.Context, tenantID, integrationID string) (string, error) {
	apiKey, err := p.tenantAPIKey(ctx, tenantID)
	if err != nil {
		return "", err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, p.baseURL+"/public/v1/integrations", nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Authorization", apiKey)
	req.Header.Set("X-VolumeOS-Tenant", tenantID)
	response, err := p.httpClient.Do(req)
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("Postiz integration lookup returned %d", response.StatusCode)
	}
	var integrations []struct {
		ID       string `json:"id"`
		Disabled bool   `json:"disabled"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 2<<20)).Decode(&integrations); err != nil {
		return "", errors.New("Postiz integration lookup returned invalid JSON")
	}
	for _, integration := range integrations {
		if integration.ID == integrationID {
			if integration.Disabled {
				return "", ErrIntegrationDisabled
			}
			return integration.ID, nil
		}
	}
	return "", ErrIntegrationNotFound
}
