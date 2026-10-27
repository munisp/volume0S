package main

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"regexp"
	"slices"
	"strings"
)

var (
	tenantPattern      = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$`)
	integrationPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$`)
)

type API struct {
	serviceToken string
	keycloak     *KeycloakClient
	postiz       *PostizClient
}

func (a *API) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "configured"})
	})
	mux.Handle("POST /v1/tenants", a.authenticate(http.HandlerFunc(a.createIdentity)))
	mux.Handle("DELETE /v1/tenants/{tenant}", a.authenticate(http.HandlerFunc(a.deleteIdentity)))
	mux.Handle("POST /v1/tenant-channels", a.authenticate(http.HandlerFunc(a.validateChannel)))
	return recoverPanic(logRequests(mux))
}

func (a *API) authenticate(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		header := r.Header.Get("Authorization")
		provided := strings.TrimPrefix(header, "Bearer ")
		tenantID := r.Header.Get("X-VolumeOS-Tenant")
		if !strings.HasPrefix(header, "Bearer ") || len(a.serviceToken) < 32 ||
			subtle.ConstantTimeCompare([]byte(provided), []byte(a.serviceToken)) != 1 || !tenantPattern.MatchString(tenantID) {
			writeError(w, http.StatusUnauthorized, "unauthorized", "valid scoped service credentials are required")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func decode(w http.ResponseWriter, r *http.Request, target any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 64*1024)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json", "one valid JSON object is required")
		return false
	}
	if decoder.More() {
		writeError(w, http.StatusBadRequest, "invalid_json", "one JSON object is required")
		return false
	}
	return true
}

func (a *API) createIdentity(w http.ResponseWriter, r *http.Request) {
	var input struct {
		TenantID string `json:"tenant_id"`
		Name     string `json:"name"`
		Corridor string `json:"corridor"`
	}
	if !decode(w, r, &input) {
		return
	}
	if input.TenantID != r.Header.Get("X-VolumeOS-Tenant") || !tenantPattern.MatchString(input.TenantID) ||
		len(strings.TrimSpace(input.Name)) < 2 || len(input.Name) > 120 || !tenantPattern.MatchString(input.Corridor) {
		writeError(w, http.StatusBadRequest, "invalid_request", "valid matching tenant, name, and corridor are required")
		return
	}
	clientID, created, err := a.keycloak.EnsureTenant(r.Context(), input.TenantID, strings.TrimSpace(input.Name), input.Corridor)
	if err != nil {
		writeError(w, http.StatusBadGateway, "identity_provisioning_failed", "Keycloak could not provision the tenant")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"client_id": clientID, "created": created})
}

func (a *API) deleteIdentity(w http.ResponseWriter, r *http.Request) {
	if r.PathValue("tenant") != r.Header.Get("X-VolumeOS-Tenant") {
		writeError(w, http.StatusForbidden, "tenant_mismatch", "tenant path and credential scope must match")
		return
	}
	if err := a.keycloak.DeleteTenant(r.Context(), r.PathValue("tenant")); err != nil {
		writeError(w, http.StatusBadGateway, "identity_compensation_failed", "Keycloak could not remove the tenant client")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (a *API) validateChannel(w http.ResponseWriter, r *http.Request) {
	var input struct {
		TenantID      string   `json:"tenant_id"`
		IntegrationID string   `json:"integration_id"`
		Offers        []string `json:"offers"`
	}
	if !decode(w, r, &input) {
		return
	}
	if input.TenantID != r.Header.Get("X-VolumeOS-Tenant") || !tenantPattern.MatchString(input.TenantID) ||
		!integrationPattern.MatchString(input.IntegrationID) || !slices.Contains(input.Offers, "02") {
		writeError(w, http.StatusBadRequest, "invalid_request", "matching tenant, integration ID, and offer 02 are required")
		return
	}
	channelID, err := a.postiz.ValidateIntegration(r.Context(), input.TenantID, input.IntegrationID)
	if errors.Is(err, ErrIntegrationNotFound) || errors.Is(err, ErrIntegrationDisabled) {
		writeError(w, http.StatusUnprocessableEntity, "integration_unavailable", err.Error())
		return
	}
	if err != nil {
		writeError(w, http.StatusBadGateway, "channel_validation_failed", "Postiz could not validate the tenant channel")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"channel_id": channelID, "status": "validated"})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{"error": map[string]string{"code": code, "message": message}})
}

func logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		slog.InfoContext(r.Context(), "request", "method", r.Method, "path", r.URL.Path,
			"tenant_id", r.Header.Get("X-VolumeOS-Tenant"))
		next.ServeHTTP(w, r)
	})
}

func recoverPanic(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if recover() != nil {
				writeError(w, http.StatusInternalServerError, "internal", "request failed")
			}
		}()
		next.ServeHTTP(w, r)
	})
}
