package main

import (
	"context"
	"strings"
	"sync"
	"time"
)

// MemoryStore is explicitly for unit tests and local development only.
type MemoryStore struct {
	mu      sync.RWMutex
	tenants map[string]Tenant
}

func NewMemoryStore() *MemoryStore                 { return &MemoryStore{tenants: make(map[string]Tenant)} }
func (s *MemoryStore) Ready(context.Context) error { return nil }

func cloneTenant(t Tenant) Tenant {
	t.Workspaces = append([]Workspace(nil), t.Workspaces...)
	t.Subscriptions = append([]Subscription(nil), t.Subscriptions...)
	return t
}

func (s *MemoryStore) CreateTenant(_ context.Context, t Tenant) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, exists := s.tenants[t.ID]; exists {
		return ErrConflict
	}
	for _, existing := range s.tenants {
		if existing.Slug == t.Slug {
			return ErrConflict
		}
	}
	s.tenants[t.ID] = cloneTenant(t)
	return nil
}

func (s *MemoryStore) Tenant(_ context.Context, id string) (Tenant, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	t, ok := s.tenants[id]
	if !ok {
		return Tenant{}, ErrNotFound
	}
	return cloneTenant(t), nil
}

func (s *MemoryStore) TransitionTenant(_ context.Context, id string, to Lifecycle, version int64, _ string) (Tenant, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.tenants[id]
	if !ok {
		return Tenant{}, ErrNotFound
	}
	if t.Version != version {
		return Tenant{}, ErrConflict
	}
	if !CanTransition(t.State, to) {
		return Tenant{}, ErrInvalidChange
	}
	t.State, t.Version, t.UpdatedAt = to, t.Version+1, time.Now().UTC()
	s.tenants[id] = t
	return cloneTenant(t), nil
}

func (s *MemoryStore) SetOutboundSuspended(_ context.Context, id string, suspended bool, _ string) (Tenant, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.tenants[id]
	if !ok {
		return Tenant{}, ErrNotFound
	}
	if t.State == Offboarded && !suspended {
		return Tenant{}, ErrInvalidChange
	}
	t.OutboundSuspended, t.Version, t.UpdatedAt = suspended, t.Version+1, time.Now().UTC()
	s.tenants[id] = t
	return cloneTenant(t), nil
}

func (s *MemoryStore) CreateWorkspace(_ context.Context, tenantID, name string) (Workspace, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.tenants[tenantID]
	if !ok {
		return Workspace{}, ErrNotFound
	}
	if t.State == Offboarded || strings.TrimSpace(name) == "" {
		return Workspace{}, ErrInvalidChange
	}
	w := Workspace{ID: newID(), TenantID: tenantID, Name: strings.TrimSpace(name), CreatedAt: time.Now().UTC()}
	t.Workspaces = append(t.Workspaces, w)
	t.Version++
	s.tenants[tenantID] = t
	return w, nil
}

func (s *MemoryStore) CreateSubscription(_ context.Context, tenantID, offer, tier string) (Subscription, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.tenants[tenantID]
	if !ok {
		return Subscription{}, ErrNotFound
	}
	if err := ValidateSubscription(offer, tier); err != nil || t.State == Offboarded {
		return Subscription{}, ErrInvalidChange
	}
	for _, existing := range t.Subscriptions {
		if existing.Offer == offer {
			return Subscription{}, ErrConflict
		}
	}
	sub := Subscription{ID: newID(), TenantID: tenantID, Offer: offer, Tier: tier, CreatedAt: time.Now().UTC()}
	t.Subscriptions = append(t.Subscriptions, sub)
	t.Version++
	s.tenants[tenantID] = t
	return sub, nil
}
