package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestPostizUsesTenantCredentialAndValidatesEnabledIntegration(t *testing.T) {
	secretRequests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/dapr/v1.0/secrets/kubernetes/postiz-tenant-a-api-key":
			secretRequests++
			_, _ = w.Write([]byte(`{"postiz-tenant-a-api-key":"tenant-a-key"}`))
		case "/postiz/public/v1/integrations":
			if r.Header.Get("Authorization") != "tenant-a-key" || r.Header.Get("X-VolumeOS-Tenant") != "tenant-a" {
				t.Error("tenant-scoped Postiz credentials were not used")
			}
			_, _ = w.Write([]byte(`[{"id":"channel-1","disabled":false},{"id":"disabled-1","disabled":true}]`))
		default:
			t.Errorf("unexpected path: %s", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	client := &PostizClient{
		baseURL: server.URL + "/postiz", daprURL: server.URL + "/dapr", secretStore: "kubernetes",
		httpClient: &http.Client{Timeout: time.Second},
	}
	channel, err := client.ValidateIntegration(context.Background(), "tenant-a", "channel-1")
	if err != nil || channel != "channel-1" {
		t.Fatalf("validate: %s %v", channel, err)
	}
	if _, err := client.ValidateIntegration(context.Background(), "tenant-a", "other-tenant-channel"); !errors.Is(err, ErrIntegrationNotFound) {
		t.Fatalf("unknown integration was accepted: %v", err)
	}
	if _, err := client.ValidateIntegration(context.Background(), "tenant-a", "disabled-1"); !errors.Is(err, ErrIntegrationDisabled) {
		t.Fatalf("disabled integration was accepted: %v", err)
	}
	if secretRequests != 3 {
		t.Fatalf("tenant credential was not retrieved for each validation: %d", secretRequests)
	}
}

func TestProvisioningAPIValidatesConnectedChannel(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1.0/secrets/tenant-secrets/postiz-tenant-a-api-key":
			_, _ = w.Write([]byte(`{"postiz-tenant-a-api-key":"tenant-a-key"}`))
		case "/api/public/v1/integrations":
			if r.Header.Get("Authorization") != "tenant-a-key" {
				t.Error("wrong tenant credential")
			}
			_, _ = w.Write([]byte(`[{"id":"channel-1","disabled":false}]`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	api := &API{
		serviceToken: "0123456789abcdef0123456789abcdef",
		postiz: &PostizClient{
			baseURL: server.URL + "/api", daprURL: server.URL, secretStore: "tenant-secrets",
			httpClient: &http.Client{Timeout: time.Second},
		},
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/tenant-channels", strings.NewReader(
		`{"tenant_id":"tenant-a","integration_id":"channel-1","offers":["01","02"]}`,
	))
	request.Header.Set("X-VolumeOS-Tenant", "tenant-a")
	request.Header.Set("Authorization", "Bearer 0123456789abcdef0123456789abcdef")
	response := httptest.NewRecorder()
	api.routes().ServeHTTP(response, request)
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"channel_id":"channel-1"`) {
		t.Fatalf("connected channel rejected: %d %s", response.Code, response.Body.String())
	}
}

func TestProvisioningAPIRejectsMissingAndMismatchedCredentials(t *testing.T) {
	api := &API{serviceToken: "0123456789abcdef0123456789abcdef"}
	handler := api.routes()
	request := httptest.NewRequest(http.MethodPost, "/v1/tenant-channels", nil)
	request.Header.Set("X-VolumeOS-Tenant", "tenant-a")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("missing token: %d", response.Code)
	}
	request = httptest.NewRequest(http.MethodPost, "/v1/tenant-channels", nil)
	request.Header.Set("X-VolumeOS-Tenant", "tenant-a")
	request.Header.Set("Authorization", "Bearer 0123456789abcdef0123456789abcdef")
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusBadRequest {
		t.Fatalf("invalid body: %d %s", response.Code, response.Body.String())
	}
}
