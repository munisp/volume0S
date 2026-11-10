package main

import (
	"encoding/json"
	"net/http"
	"testing"
)

func TestScopeClaimsOnlyLetsPlatformOperatorsActOnAnotherBusiness(t *testing.T) {
	platform := Claims{TenantID: "platform", Roles: map[string]bool{"super-operator": true}}
	scoped, err := scopeClaims(platform, "acme-ltd")
	if err != nil || scoped.TenantID != "acme-ltd" || !scoped.Platform {
		t.Fatalf("platform operator acting on acme-ltd: %+v %v", scoped, err)
	}
	if own, _ := scopeClaims(platform, ""); own.TenantID != "platform" || !own.Platform {
		t.Fatalf("platform operator without a choice keeps the platform scope: %+v", own)
	}
	if _, err := scopeClaims(platform, "../etc"); err == nil {
		t.Fatal("an invalid business id must be refused")
	}
	// A business's own super operator, and a platform account without super-operator, cannot switch business.
	for _, c := range []Claims{
		{TenantID: "demo-tenant", Roles: map[string]bool{"super-operator": true}},
		{TenantID: "platform", Roles: map[string]bool{"delivery": true}},
	} {
		got, err := scopeClaims(c, "acme-ltd")
		if err != nil || got.TenantID != c.TenantID || got.Platform {
			t.Fatalf("%+v must keep its own business, got %+v %v", c, got, err)
		}
	}
}

func TestListTenantsIsForPlatformOperatorsOnly(t *testing.T) {
	handler, _ := testServer(t)
	createTenant(t, handler, "tenant-a")
	createTenant(t, handler, "tenant-b")
	if r := request(t, handler, http.MethodPost, "/v1/subscriptions", "tenant-a", "sales", `{"offer":"01","tier":"starter"}`); r.Code != http.StatusCreated {
		t.Fatalf("subscribe: %d %s", r.Code, r.Body.String())
	}
	for _, scope := range [][2]string{{"tenant-a", "super-operator"}, {"platform", "delivery"}} {
		if r := request(t, handler, http.MethodGet, "/v1/tenants", scope[0], scope[1], ""); r.Code != http.StatusForbidden {
			t.Fatalf("%v listing businesses: %d", scope, r.Code)
		}
	}
	r := request(t, handler, http.MethodGet, "/v1/tenants", "platform", "super-operator", "")
	if r.Code != http.StatusOK {
		t.Fatalf("platform list: %d %s", r.Code, r.Body.String())
	}
	var body struct{ Tenants []TenantSummary }
	if err := json.Unmarshal(r.Body.Bytes(), &body); err != nil || len(body.Tenants) != 2 {
		t.Fatalf("list: %s", r.Body.String())
	}
	for _, row := range body.Tenants {
		if row.ID == "tenant-a" && (len(row.Offers) != 1 || row.Offers[0] != "01") {
			t.Fatalf("tenant-a offers: %+v", row)
		}
	}
}

func TestFinanceCanReadItsBusiness(t *testing.T) {
	handler, _ := testServer(t)
	createTenant(t, handler, "tenant-a")
	if r := request(t, handler, http.MethodGet, "/v1/tenants/current", "tenant-a", "finance", ""); r.Code != http.StatusOK {
		t.Fatalf("finance reading its business: %d", r.Code)
	}
}
