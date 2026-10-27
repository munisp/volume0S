package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestKeycloakTenantLifecycleIsIdempotent(t *testing.T) {
	created := 0
	deleted := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/token":
			if err := r.ParseForm(); err != nil || r.Form.Get("client_secret") != "admin-secret" {
				t.Error("client credentials were not sent correctly")
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "admin-token", "expires_in": 300})
		case r.URL.Path == "/admin/clients" && r.Method == http.MethodGet:
			if r.Header.Get("Authorization") != "Bearer admin-token" {
				t.Error("admin token missing")
			}
			if created == 0 {
				_, _ = w.Write([]byte(`[]`))
			} else {
				_, _ = w.Write([]byte(`[{"id":"internal-1","clientId":"volumeos-tenant-a"}]`))
			}
		case r.URL.Path == "/admin/clients" && r.Method == http.MethodPost:
			created++
			var payload map[string]any
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Fatal(err)
			}
			if payload["clientId"] != "volumeos-tenant-a" || payload["standardFlowEnabled"] != false {
				t.Errorf("unsafe client configuration: %+v", payload)
			}
			mappers := payload["protocolMappers"].([]any)
			if !strings.Contains(mappers[0].(map[string]any)["config"].(map[string]any)["claim.value"].(string), "tenant-a") {
				t.Error("tenant claim mapper missing")
			}
			w.WriteHeader(http.StatusCreated)
		case r.URL.Path == "/admin/clients/internal-1" && r.Method == http.MethodDelete:
			deleted++
			w.WriteHeader(http.StatusNoContent)
		default:
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	client := &KeycloakClient{
		adminURL: server.URL + "/admin", tokenURL: server.URL + "/token",
		clientID: "provisioner", clientSecret: "admin-secret", httpClient: &http.Client{Timeout: time.Second},
	}
	clientID, owned, err := client.EnsureTenant(context.Background(), "tenant-a", "Demo Gym", "lagos-yaba")
	if err != nil || clientID != "volumeos-tenant-a" || !owned {
		t.Fatalf("ensure: %s owned=%t %v", clientID, owned, err)
	}
	_, owned, err = client.EnsureTenant(context.Background(), "tenant-a", "Demo Gym", "lagos-yaba")
	if err != nil {
		t.Fatal(err)
	}
	if owned {
		t.Fatal("reused Keycloak client was incorrectly marked as onboarding-owned")
	}
	if created != 1 {
		t.Fatalf("duplicate clients created: %d", created)
	}
	if err := client.DeleteTenant(context.Background(), "tenant-a"); err != nil {
		t.Fatal(err)
	}
	if deleted != 1 {
		t.Fatalf("client was not deleted: %d", deleted)
	}
}
