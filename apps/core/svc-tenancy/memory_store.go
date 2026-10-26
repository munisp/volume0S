package main

import (
	"context"
	"strings"
	"sync"
	"time"
)

// MemoryStore is explicitly for unit tests and local development only.
type MemoryStore struct {
	mu           sync.RWMutex
	tenants      map[string]Tenant
	dispatches   map[string]DispatchResult
	suppressions map[string]string
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{
		tenants: make(map[string]Tenant), dispatches: make(map[string]DispatchResult),
		suppressions: make(map[string]string),
	}
}
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

func normalizeRecipient(value string) string { return strings.ToLower(strings.TrimSpace(value)) }

func suppressionKey(tenantID, channel, recipient string) string {
	return tenantID + "\x00" + channel + "\x00" + normalizeRecipient(recipient)
}

func validMessagingChannel(channel string) bool {
	switch channel {
	case "email", "sms", "whatsapp", "social":
		return true
	default:
		return false
	}
}

func validCurrency(currency string) bool {
	if len(currency) != 3 {
		return false
	}
	for _, character := range currency {
		if character < 'A' || character > 'Z' {
			return false
		}
	}
	return true
}

func validSuppressionInput(channel, recipient, reason string, suppressed bool) bool {
	reason = strings.TrimSpace(reason)
	return validMessagingChannel(channel) && normalizeRecipient(recipient) != "" && len(reason) <= 500 && (!suppressed || reason != "")
}

func (s *MemoryStore) AuthorizeDispatch(_ context.Context, tenantID, channel, recipient string) (DispatchDecision, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if !validMessagingChannel(channel) || (channel != "social" && normalizeRecipient(recipient) == "") {
		return DispatchDecision{}, ErrInvalidChange
	}
	tenant, ok := s.tenants[tenantID]
	if !ok {
		return DispatchDecision{}, ErrNotFound
	}
	if tenant.State != Active {
		return DispatchDecision{Allowed: false, Reason: "tenant_not_active"}, nil
	}
	if tenant.OutboundSuspended {
		return DispatchDecision{Allowed: false, Reason: "tenant_suspended"}, nil
	}
	if _, suppressed := s.suppressions[suppressionKey(tenantID, channel, recipient)]; suppressed {
		return DispatchDecision{Allowed: false, Reason: "recipient_suppressed"}, nil
	}
	return DispatchDecision{Allowed: true, Reason: "authorized"}, nil
}

func (s *MemoryStore) SetSuppression(_ context.Context, tenantID, channel, recipient string, suppressed bool, reason string) error {
	if !validSuppressionInput(channel, recipient, reason, suppressed) {
		return ErrInvalidChange
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.tenants[tenantID]; !ok {
		return ErrNotFound
	}
	key := suppressionKey(tenantID, channel, recipient)
	if suppressed {
		s.suppressions[key] = strings.TrimSpace(reason)
	} else {
		delete(s.suppressions, key)
	}
	return nil
}

func (s *MemoryStore) GetDispatchResult(_ context.Context, tenantID, key string) (DispatchResult, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result, ok := s.dispatches[tenantID+"\x00"+key]
	if !ok {
		return DispatchResult{}, ErrNotFound
	}
	return result, nil
}

func (s *MemoryStore) PutDispatchResult(_ context.Context, value DispatchResult) (DispatchResult, error) {
	if value.Key == "" || len(value.Key) > 512 || !tenantIDPattern.MatchString(value.TenantID) ||
		strings.TrimSpace(value.ProviderID) == "" || len(value.ProviderID) > 512 || value.CostMinor < 0 || !validCurrency(value.Currency) {
		return DispatchResult{}, ErrInvalidChange
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.tenants[value.TenantID]; !ok {
		return DispatchResult{}, ErrNotFound
	}
	key := value.TenantID + "\x00" + value.Key
	if existing, ok := s.dispatches[key]; ok {
		if existing.ProviderID != value.ProviderID || existing.CostMinor != value.CostMinor || existing.Currency != value.Currency {
			return DispatchResult{}, ErrConflict
		}
		return existing, nil
	}
	value.RecordedAt = time.Now().UTC()
	s.dispatches[key] = value
	return value, nil
}
