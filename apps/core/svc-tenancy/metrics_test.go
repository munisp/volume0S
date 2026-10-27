package main

import (
	"net/http"
	"strings"
	"testing"
)

func TestRequestMetricsUseRouteTemplatesAndStatusCodes(t *testing.T) {
	handler, _ := testServer(t)
	createTenant(t, handler, "tenant-a")
	missing := request(t, handler, http.MethodGet, "/v1/tenants/current", "tenant-b", "reader", "")
	if missing.Code != http.StatusNotFound {
		t.Fatalf("expected not found, got %d", missing.Code)
	}
	unmatched := request(t, handler, http.MethodGet, "/v1/not-a-route?tenant_id=secret", "tenant-a", "reader", "")
	if unmatched.Code != http.StatusNotFound {
		t.Fatalf("expected unmatched route, got %d", unmatched.Code)
	}
	request(t, handler, http.MethodGet, "/healthz", "", "", "")

	scrape := request(t, handler, http.MethodGet, "/metrics", "", "", "")
	if scrape.Code != http.StatusOK {
		t.Fatalf("metrics scrape: %d", scrape.Code)
	}
	body := scrape.Body.String()
	for _, expected := range []string{
		"# TYPE volumeos_http_requests_total counter",
		`method="POST",route="/v1/tenants/current",code="201"} 1`,
		`method="GET",route="/v1/tenants/current",code="404"} 1`,
		`method="GET",route="unmatched",code="404"} 1`,
	} {
		if !strings.Contains(body, expected) {
			t.Fatalf("metrics missing %q:\n%s", expected, body)
		}
	}
	for _, forbidden := range []string{"tenant-a", "tenant-b", "tenant_id=secret", "/healthz", "/metrics"} {
		if strings.Contains(body, forbidden) {
			t.Fatalf("metrics leaked forbidden label or scrape traffic %q:\n%s", forbidden, body)
		}
	}
}
