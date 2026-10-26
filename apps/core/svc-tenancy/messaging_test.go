package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func activateTenant(t *testing.T, store *MemoryStore, id string) {
	t.Helper()
	if _, err := store.TransitionTenant(context.Background(), id, Onboarding, 1, "test onboarding"); err != nil {
		t.Fatal(err)
	}
	if _, err := store.TransitionTenant(context.Background(), id, Active, 2, "test activation"); err != nil {
		t.Fatal(err)
	}
}

func authorize(t *testing.T, handler http.Handler, tenant, recipient string) DispatchDecision {
	t.Helper()
	body := `{"channel":"email","recipient":` + strconvQuote(recipient) + `}`
	response := request(t, handler, http.MethodPost, "/v1/dispatch-authorizations", tenant, "delivery", body)
	if response.Code != http.StatusOK {
		t.Fatalf("authorize: %d %s", response.Code, response.Body.String())
	}
	var decision DispatchDecision
	if err := json.Unmarshal(response.Body.Bytes(), &decision); err != nil {
		t.Fatal(err)
	}
	return decision
}

func strconvQuote(value string) string {
	encoded, _ := json.Marshal(value)
	return string(encoded)
}

func TestDispatchPolicyUsesLifecycleKillSwitchAndSuppression(t *testing.T) {
	handler, store := testServer(t)
	createTenant(t, handler, "tenant-a")
	if decision := authorize(t, handler, "tenant-a", "owner@example.com"); decision.Allowed || decision.Reason != "tenant_not_active" {
		t.Fatalf("inactive tenant was not denied: %+v", decision)
	}
	activateTenant(t, store, "tenant-a")
	if decision := authorize(t, handler, "tenant-a", "owner@example.com"); !decision.Allowed {
		t.Fatalf("active tenant was denied: %+v", decision)
	}
	suppressed := request(t, handler, http.MethodPut, "/v1/suppressions", "tenant-a", "super-operator",
		`{"channel":"email","recipient":" Owner@Example.com ","reason":"customer opt-out"}`)
	if suppressed.Code != http.StatusOK {
		t.Fatalf("suppress: %d %s", suppressed.Code, suppressed.Body.String())
	}
	if decision := authorize(t, handler, "tenant-a", "owner@example.com"); decision.Allowed || decision.Reason != "recipient_suppressed" {
		t.Fatalf("suppression was not enforced: %+v", decision)
	}
	restored := request(t, handler, http.MethodDelete, "/v1/suppressions", "tenant-a", "super-operator",
		`{"channel":"email","recipient":"owner@example.com"}`)
	if restored.Code != http.StatusOK {
		t.Fatalf("restore: %d %s", restored.Code, restored.Body.String())
	}
	if _, err := store.SetOutboundSuspended(context.Background(), "tenant-a", true, "incident"); err != nil {
		t.Fatal(err)
	}
	if decision := authorize(t, handler, "tenant-a", "owner@example.com"); decision.Allowed || decision.Reason != "tenant_suspended" {
		t.Fatalf("kill switch was not enforced: %+v", decision)
	}
}

func TestDispatchResultsArePersistentIdempotentAndTenantScoped(t *testing.T) {
	handler, store := testServer(t)
	createTenant(t, handler, "tenant-a")
	createTenant(t, handler, "tenant-b")
	body := `{"key":"tenant-a:campaign-1:email","provider_id":"provider-1","cost_minor":760,"currency":"NGN"}`
	first := request(t, handler, http.MethodPut, "/v1/dispatch-results", "tenant-a", "delivery", body)
	second := request(t, handler, http.MethodPut, "/v1/dispatch-results", "tenant-a", "delivery", body)
	if first.Code != http.StatusOK || second.Code != http.StatusOK {
		t.Fatalf("idempotent writes failed: %d %d", first.Code, second.Code)
	}
	missing := request(t, handler, http.MethodGet, "/v1/dispatch-results?key=tenant-a:campaign-1:email", "tenant-b", "delivery", "")
	if missing.Code != http.StatusNotFound {
		t.Fatalf("cross-tenant idempotency read: %d", missing.Code)
	}
	conflict := request(t, handler, http.MethodPut, "/v1/dispatch-results", "tenant-a", "delivery",
		`{"key":"tenant-a:campaign-1:email","provider_id":"different","cost_minor":760,"currency":"NGN"}`)
	if conflict.Code != http.StatusConflict {
		t.Fatalf("conflicting replay: %d %s", conflict.Code, conflict.Body.String())
	}
	invalidCurrency := request(t, handler, http.MethodPut, "/v1/dispatch-results", "tenant-a", "delivery",
		`{"key":"tenant-a:campaign-2:email","provider_id":"provider-2","cost_minor":760,"currency":"ngn"}`)
	if invalidCurrency.Code != http.StatusUnprocessableEntity {
		t.Fatalf("invalid currency: %d %s", invalidCurrency.Code, invalidCurrency.Body.String())
	}
	if _, err := store.GetDispatchResult(context.Background(), "tenant-a", "tenant-a:campaign-1:email"); err != nil {
		t.Fatal(err)
	}
}

func TestServiceTokenSupportsScopedInternalCalls(t *testing.T) {
	t.Setenv("VOLUMEOS_SERVICE_TOKEN", "0123456789abcdef0123456789abcdef")
	t.Setenv("VO_ENV", "prod")
	auth, err := NewAuthenticator()
	if err != nil {
		t.Fatal(err)
	}
	store := NewMemoryStore()
	handler := (&API{store: store, auth: auth}).routes()
	tenant, err := NewTenant("tenant-a", "Test Gym", "test-gym")
	if err != nil {
		t.Fatal(err)
	}
	if err := store.CreateTenant(context.Background(), tenant); err != nil {
		t.Fatal(err)
	}
	response := request(t, handler, http.MethodGet, "/v1/tenants/current", "tenant-a", "", "")
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("missing service token: %d", response.Code)
	}
	withToken := httptest.NewRequest(http.MethodGet, "/v1/tenants/current", nil)
	withToken.Header.Set("X-VolumeOS-Tenant", "tenant-a")
	withToken.Header.Set("Authorization", "Bearer 0123456789abcdef0123456789abcdef")
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, withToken)
	if recorder.Code != http.StatusOK {
		t.Fatalf("service token: %d %s", recorder.Code, recorder.Body.String())
	}
}
