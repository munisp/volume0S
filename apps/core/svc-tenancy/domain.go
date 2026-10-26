package main

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"
)

var (
	ErrNotFound      = errors.New("resource not found")
	ErrConflict      = errors.New("resource conflict")
	ErrInvalidChange = errors.New("invalid state change")
)

type Lifecycle string

const (
	Prospect   Lifecycle = "prospect"
	Onboarding Lifecycle = "onboarding"
	Active     Lifecycle = "active"
	Paused     Lifecycle = "paused"
	Offboarded Lifecycle = "offboarded"
)

type Tenant struct {
	ID                string         `json:"id"`
	Name              string         `json:"name"`
	Slug              string         `json:"slug"`
	State             Lifecycle      `json:"state"`
	OutboundSuspended bool           `json:"outbound_suspended"`
	Version           int64          `json:"version"`
	CreatedAt         time.Time      `json:"created_at"`
	UpdatedAt         time.Time      `json:"updated_at"`
	Workspaces        []Workspace    `json:"workspaces"`
	Subscriptions     []Subscription `json:"subscriptions"`
}

type Workspace struct {
	ID        string    `json:"id"`
	TenantID  string    `json:"tenant_id"`
	Name      string    `json:"name"`
	CreatedAt time.Time `json:"created_at"`
}

type Subscription struct {
	ID        string    `json:"id"`
	TenantID  string    `json:"tenant_id"`
	Offer     string    `json:"offer"`
	Tier      string    `json:"tier"`
	CreatedAt time.Time `json:"created_at"`
}

type DispatchDecision struct {
	Allowed bool   `json:"allowed"`
	Reason  string `json:"reason"`
}

type DispatchResult struct {
	Key        string    `json:"key"`
	TenantID   string    `json:"tenant_id"`
	ProviderID string    `json:"provider_id"`
	CostMinor  int64     `json:"cost_minor"`
	Currency   string    `json:"currency"`
	RecordedAt time.Time `json:"recorded_at"`
}

type Store interface {
	Ready(context.Context) error
	CreateTenant(context.Context, Tenant) error
	Tenant(context.Context, string) (Tenant, error)
	TransitionTenant(context.Context, string, Lifecycle, int64, string) (Tenant, error)
	SetOutboundSuspended(context.Context, string, bool, string) (Tenant, error)
	CreateWorkspace(context.Context, string, string) (Workspace, error)
	CreateSubscription(context.Context, string, string, string) (Subscription, error)
	AuthorizeDispatch(context.Context, string, string, string) (DispatchDecision, error)
	SetSuppression(context.Context, string, string, string, bool, string) error
	GetDispatchResult(context.Context, string, string) (DispatchResult, error)
	PutDispatchResult(context.Context, DispatchResult) (DispatchResult, error)
}

var (
	tenantIDPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$`)
	slugPattern     = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{1,62}$`)
)

func NewTenant(id, name, slug string) (Tenant, error) {
	id, name, slug = strings.TrimSpace(id), strings.TrimSpace(name), strings.TrimSpace(slug)
	if !tenantIDPattern.MatchString(id) {
		return Tenant{}, fmt.Errorf("tenant id must contain 1 to 128 safe identifier characters")
	}
	if len(name) < 2 || len(name) > 120 {
		return Tenant{}, fmt.Errorf("tenant name must contain 2 to 120 characters")
	}
	if !slugPattern.MatchString(slug) {
		return Tenant{}, fmt.Errorf("slug must match %s", slugPattern.String())
	}
	now := time.Now().UTC()
	return Tenant{
		ID: id, Name: name, Slug: slug, State: Prospect, Version: 1,
		CreatedAt: now, UpdatedAt: now, Workspaces: []Workspace{}, Subscriptions: []Subscription{},
	}, nil
}

func CanTransition(from, to Lifecycle) bool {
	if from == Offboarded || from == to {
		return false
	}
	if to == Offboarded {
		return true
	}
	switch from {
	case Prospect:
		return to == Onboarding
	case Onboarding:
		return to == Active
	case Active:
		return to == Paused
	case Paused:
		return to == Active
	default:
		return false
	}
}

func ValidateSubscription(offer, tier string) error {
	if offer < "01" || offer > "08" || len(offer) != 2 {
		return fmt.Errorf("offer must be one of 01 through 08")
	}
	switch tier {
	case "starter", "growth", "pro":
		return nil
	default:
		return fmt.Errorf("tier must be starter, growth, or pro")
	}
}

func newID() string {
	var id [16]byte
	if _, err := rand.Read(id[:]); err != nil {
		panic(fmt.Sprintf("generate random identifier: %v", err))
	}
	id[6] = (id[6] & 0x0f) | 0x40
	id[8] = (id[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		id[0:4], id[4:6], id[6:8], id[8:10], id[10:16])
}
