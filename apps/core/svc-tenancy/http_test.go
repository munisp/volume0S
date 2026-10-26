package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func testServer(t *testing.T) (http.Handler, *MemoryStore) {
	t.Helper()
	t.Setenv("ALLOW_INSECURE_HEADER_AUTH", "true")
	t.Setenv("VO_ENV", "dev")
	auth, err := NewAuthenticator()
	if err != nil {
		t.Fatal(err)
	}
	store := NewMemoryStore()
	return (&API{store: store, auth: auth}).routes(), store
}

func request(t *testing.T, handler http.Handler, method, path, tenant, roles, body string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(method, path, bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	if tenant != "" {
		req.Header.Set("X-VolumeOS-Tenant", tenant)
	}
	req.Header.Set("X-VolumeOS-Roles", roles)
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, req)
	return response
}

func createTenant(t *testing.T, handler http.Handler, id string) Tenant {
	t.Helper()
	response := request(t, handler, http.MethodPost, "/v1/tenants/current", id, "super-operator", `{"name":"Test Gym","slug":"test-gym-`+id+`"}`)
	if response.Code != http.StatusCreated {
		t.Fatalf("create: %d %s", response.Code, response.Body.String())
	}
	var tenant Tenant
	if err := json.Unmarshal(response.Body.Bytes(), &tenant); err != nil {
		t.Fatal(err)
	}
	return tenant
}

func TestTenantIsolationAndRBAC(t *testing.T) {
	handler, _ := testServer(t)
	createTenant(t, handler, "tenant-a")
	createTenant(t, handler, "tenant-b")

	missing := request(t, handler, http.MethodGet, "/v1/tenants/current", "", "reader", "")
	if missing.Code != http.StatusUnauthorized {
		t.Fatalf("missing scope: %d", missing.Code)
	}
	crossTenant := request(t, handler, http.MethodGet, "/v1/tenants/current", "tenant-c", "reader", "")
	if crossTenant.Code != http.StatusNotFound {
		t.Fatalf("cross-tenant read: %d", crossTenant.Code)
	}
	forbidden := request(t, handler, http.MethodPost, "/v1/subscriptions", "tenant-a", "delivery", `{"offer":"02","tier":"growth"}`)
	if forbidden.Code != http.StatusForbidden {
		t.Fatalf("RBAC: %d", forbidden.Code)
	}
}

func TestKillSwitchRequiresReasonAndPersists(t *testing.T) {
	handler, _ := testServer(t)
	createTenant(t, handler, "tenant-a")
	bad := request(t, handler, http.MethodPut, "/v1/tenants/current/outbound-suspension", "tenant-a", "super-operator", `{"suspended":true,"reason":""}`)
	if bad.Code != http.StatusBadRequest {
		t.Fatalf("missing reason: %d", bad.Code)
	}
	good := request(t, handler, http.MethodPut, "/v1/tenants/current/outbound-suspension", "tenant-a", "super-operator", `{"suspended":true,"reason":"incident INC-42"}`)
	if good.Code != http.StatusOK {
		t.Fatalf("suspend: %d %s", good.Code, good.Body.String())
	}
	var tenant Tenant
	if err := json.Unmarshal(good.Body.Bytes(), &tenant); err != nil {
		t.Fatal(err)
	}
	if !tenant.OutboundSuspended {
		t.Fatal("kill switch did not persist")
	}
}

func TestLifecycleEndpoint(t *testing.T) {
	handler, _ := testServer(t)
	created := createTenant(t, handler, "tenant-a")
	response := request(t, handler, http.MethodPost, "/v1/tenants/current/transitions", "tenant-a", "super-operator", `{"to":"onboarding","version":`+jsonNumber(created.Version)+`,"reason":"contract signed"}`)
	if response.Code != http.StatusOK {
		t.Fatalf("transition: %d %s", response.Code, response.Body.String())
	}
}

func jsonNumber(value int64) string { return string(json.Number(fmtInt(value))) }
func fmtInt(value int64) string {
	if value == 1 {
		return "1"
	}
	panic("test helper only supports the initial version")
}
