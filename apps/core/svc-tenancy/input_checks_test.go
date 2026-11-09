package main

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

// BB-08: the opt-out list only accepts real addresses, says why it refuses, and says when nothing was lifted.
func TestSuppressionRecipientsAreValidated(t *testing.T) {
	handler, _ := testServer(t)
	tenant := createTenant(t, handler, "tenant-a")
	// Dispatch only consults suppressions for an active business.
	version := tenant.Version
	for _, to := range []string{"onboarding", "active"} {
		raw, _ := json.Marshal(map[string]any{"to": to, "version": version, "reason": "test"})
		response := request(t, handler, http.MethodPost, "/v1/tenants/current/transitions", "tenant-a", "super-operator", string(raw))
		var moved Tenant
		if response.Code != http.StatusOK || json.Unmarshal(response.Body.Bytes(), &moved) != nil {
			t.Fatalf("transition to %s: %d %s", to, response.Code, response.Body.String())
		}
		version = moved.Version
	}
	body := func(channel, recipient string) string {
		raw, _ := json.Marshal(map[string]string{"channel": channel, "recipient": recipient, "reason": "customer asked"})
		return string(raw)
	}
	refused := []struct{ channel, recipient, says string }{
		{"email", "not-an-email <script>", "email address"},
		{"email", "  ", "email address"},
		{"email", strings.Repeat("a", 400) + "@example.com", "email address"},
		{"email", "a\x00b@example.com", "hidden characters"},
		{"sms", "abc", "international number"},
		{"whatsapp", "+234", "international number"},
		{"whatsapp", "08012345678", "international number"},
		{"social", "two words", "without spaces"},
		{"carrier-pigeon", "x", "channel must be"},
	}
	for _, c := range refused {
		response := request(t, handler, http.MethodPut, "/v1/suppressions", "tenant-a", "super-operator", body(c.channel, c.recipient))
		if response.Code != http.StatusUnprocessableEntity || !strings.Contains(response.Body.String(), c.says) {
			t.Fatalf("%s %q: %d %s (want 422 saying %q)", c.channel, c.recipient, response.Code, response.Body.String(), c.says)
		}
	}
	accepted := []struct{ channel, recipient string }{
		{"email", " Customer@Example.com "}, {"sms", "+2348012345678"}, {"whatsapp", "+447700900123"}, {"social", "@ada_foods"},
	}
	for _, c := range accepted {
		response := request(t, handler, http.MethodPut, "/v1/suppressions", "tenant-a", "super-operator", body(c.channel, c.recipient))
		if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"updated"`) {
			t.Fatalf("%s %q: %d %s", c.channel, c.recipient, response.Code, response.Body.String())
		}
	}
	// The suppression still matches the address as the dispatch path sees it (normalisation unchanged).
	dispatch := request(t, handler, http.MethodPost, "/v1/dispatch-authorizations", "tenant-a", "delivery", `{"channel":"email","recipient":"customer@example.com"}`)
	if !strings.Contains(dispatch.Body.String(), "recipient_suppressed") {
		t.Fatalf("suppression no longer matches dispatch: %s", dispatch.Body.String())
	}
	lifted := request(t, handler, http.MethodDelete, "/v1/suppressions", "tenant-a", "super-operator", body("email", "customer@example.com"))
	if !strings.Contains(lifted.Body.String(), `"updated"`) {
		t.Fatalf("lift: %s", lifted.Body.String())
	}
	again := request(t, handler, http.MethodDelete, "/v1/suppressions", "tenant-a", "super-operator", body("email", "customer@example.com"))
	if again.Code != http.StatusOK || !strings.Contains(again.Body.String(), `"not_suppressed"`) {
		t.Fatalf("lifting a recipient that isn't suppressed must say so: %d %s", again.Code, again.Body.String())
	}
}

// BB-14: names refuse hidden characters and say what's wrong.
func TestNamesRefuseHiddenCharacters(t *testing.T) {
	handler, _ := testServer(t)
	createTenant(t, handler, "tenant-a")
	for _, name := range []string{"evil‮txt", "a\x00b", "x", strings.Repeat("n", 121)} {
		raw, _ := json.Marshal(map[string]string{"name": name})
		response := request(t, handler, http.MethodPost, "/v1/workspaces", "tenant-a", "delivery", string(raw))
		if response.Code != http.StatusUnprocessableEntity || !strings.Contains(response.Body.String(), "workspace name") {
			t.Fatalf("%q: %d %s", name, response.Code, response.Body.String())
		}
	}
	ok := request(t, handler, http.MethodPost, "/v1/workspaces", "tenant-a", "delivery", `{"name":"Ìbàdàn delivery team ₦"}`)
	if ok.Code != http.StatusCreated {
		t.Fatalf("unicode name: %d %s", ok.Code, ok.Body.String())
	}
	tenant := request(t, handler, http.MethodPost, "/v1/tenants/current", "tenant-b", "super-operator", `{"name":"Bad‮name","slug":"bad-name"}`)
	if tenant.Code == http.StatusCreated {
		t.Fatalf("tenant name with a direction override was accepted")
	}
}
