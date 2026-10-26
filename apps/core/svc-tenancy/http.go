package main

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strings"
)

type API struct {
	store Store
	auth  *Authenticator
}

func (a *API) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /v1/offers", func(w http.ResponseWriter, _ *http.Request) {
		offers, bundles := OfferCatalog()
		writeJSON(w, http.StatusOK, map[string]any{
			"price_book": "q1-founding-rate-reference", "offers": offers, "bundles": bundles,
		})
	})
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, r *http.Request) {
		if err := a.store.Ready(r.Context()); err != nil {
			writeError(w, http.StatusServiceUnavailable, "not_ready", "dependency check failed")
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "ready"})
	})
	mux.Handle("POST /v1/tenants/current", a.auth.middleware(a.role("super-operator", http.HandlerFunc(a.createTenant))))
	mux.Handle("GET /v1/tenants/current", a.auth.middleware(a.role("reader", http.HandlerFunc(a.getTenant))))
	mux.Handle("POST /v1/tenants/current/transitions", a.auth.middleware(a.role("super-operator", http.HandlerFunc(a.transitionTenant))))
	mux.Handle("PUT /v1/tenants/current/outbound-suspension", a.auth.middleware(a.role("super-operator", http.HandlerFunc(a.suspendOutbound))))
	mux.Handle("POST /v1/workspaces", a.auth.middleware(a.role("delivery", http.HandlerFunc(a.createWorkspace))))
	mux.Handle("POST /v1/subscriptions", a.auth.middleware(a.role("sales", http.HandlerFunc(a.createSubscription))))
	return recoverPanic(logRequests(mux))
}

func (a *API) role(role string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !claimsFrom(r.Context()).Can(role) {
			writeError(w, http.StatusForbidden, "forbidden", "required role: "+role)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func decode(w http.ResponseWriter, r *http.Request, target any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		writeError(w, http.StatusBadRequest, "invalid_json", err.Error())
		return false
	}
	if decoder.More() {
		writeError(w, http.StatusBadRequest, "invalid_json", "one JSON object is required")
		return false
	}
	return true
}

func (a *API) createTenant(w http.ResponseWriter, r *http.Request) {
	var input struct{ Name, Slug string }
	if !decode(w, r, &input) {
		return
	}
	tenant, err := NewTenant(claimsFrom(r.Context()).TenantID, input.Name, input.Slug)
	if err == nil {
		err = a.store.CreateTenant(r.Context(), tenant)
	}
	respond(w, r, http.StatusCreated, tenant, err)
}

func (a *API) getTenant(w http.ResponseWriter, r *http.Request) {
	tenant, err := a.store.Tenant(r.Context(), claimsFrom(r.Context()).TenantID)
	respond(w, r, http.StatusOK, tenant, err)
}

func (a *API) transitionTenant(w http.ResponseWriter, r *http.Request) {
	var input struct {
		To      Lifecycle `json:"to"`
		Version int64     `json:"version"`
		Reason  string    `json:"reason"`
	}
	if !decode(w, r, &input) {
		return
	}
	tenant, err := a.store.TransitionTenant(r.Context(), claimsFrom(r.Context()).TenantID, input.To, input.Version, input.Reason)
	respond(w, r, http.StatusOK, tenant, err)
}

func (a *API) suspendOutbound(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Suspended bool   `json:"suspended"`
		Reason    string `json:"reason"`
	}
	if !decode(w, r, &input) {
		return
	}
	if strings.TrimSpace(input.Reason) == "" {
		writeError(w, http.StatusBadRequest, "reason_required", "an audit reason is required")
		return
	}
	tenant, err := a.store.SetOutboundSuspended(r.Context(), claimsFrom(r.Context()).TenantID, input.Suspended, input.Reason)
	respond(w, r, http.StatusOK, tenant, err)
}

func (a *API) createWorkspace(w http.ResponseWriter, r *http.Request) {
	var input struct{ Name string }
	if !decode(w, r, &input) {
		return
	}
	workspace, err := a.store.CreateWorkspace(r.Context(), claimsFrom(r.Context()).TenantID, input.Name)
	respond(w, r, http.StatusCreated, workspace, err)
}

func (a *API) createSubscription(w http.ResponseWriter, r *http.Request) {
	var input struct{ Offer, Tier string }
	if !decode(w, r, &input) {
		return
	}
	subscription, err := a.store.CreateSubscription(r.Context(), claimsFrom(r.Context()).TenantID, input.Offer, input.Tier)
	respond(w, r, http.StatusCreated, subscription, err)
}

func respond(w http.ResponseWriter, r *http.Request, status int, value any, err error) {
	if err == nil {
		writeJSON(w, status, value)
		return
	}
	slog.WarnContext(r.Context(), "request rejected", "error", err, "tenant_id", claimsFrom(r.Context()).TenantID)
	switch {
	case errors.Is(err, ErrNotFound):
		writeError(w, http.StatusNotFound, "not_found", "resource not found in tenant scope")
	case errors.Is(err, ErrConflict):
		writeError(w, http.StatusConflict, "conflict", "resource exists or version is stale")
	case errors.Is(err, ErrInvalidChange):
		writeError(w, http.StatusUnprocessableEntity, "invalid_state", err.Error())
	default:
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			writeError(w, http.StatusGatewayTimeout, "dependency_timeout", "dependency did not respond in time")
		} else {
			writeError(w, http.StatusBadRequest, "invalid_request", err.Error())
		}
	}
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
		claims, _ := r.Context().Value(claimsKey).(Claims)
		slog.InfoContext(r.Context(), "request", "method", r.Method, "path", r.URL.Path, "tenant_id", claims.TenantID)
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
