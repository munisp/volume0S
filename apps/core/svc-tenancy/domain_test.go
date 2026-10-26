package main

import (
	"context"
	"testing"
)

func TestLifecycleTransitions(t *testing.T) {
	valid := [][2]Lifecycle{{Prospect, Onboarding}, {Onboarding, Active}, {Active, Paused}, {Paused, Active}, {Active, Offboarded}}
	for _, pair := range valid {
		if !CanTransition(pair[0], pair[1]) {
			t.Errorf("expected %s -> %s", pair[0], pair[1])
		}
	}
	invalid := [][2]Lifecycle{{Prospect, Active}, {Active, Onboarding}, {Offboarded, Active}, {Paused, Onboarding}}
	for _, pair := range invalid {
		if CanTransition(pair[0], pair[1]) {
			t.Errorf("rejected transition allowed: %s -> %s", pair[0], pair[1])
		}
	}
}

func TestMemoryStoreOptimisticConcurrency(t *testing.T) {
	store := NewMemoryStore()
	tenant, err := NewTenant("tenant-a", "A Gym", "a-gym")
	if err != nil {
		t.Fatal(err)
	}
	if err := store.CreateTenant(context.Background(), tenant); err != nil {
		t.Fatal(err)
	}
	if _, err := store.TransitionTenant(context.Background(), tenant.ID, Onboarding, 1, "contract signed"); err != nil {
		t.Fatal(err)
	}
	if _, err := store.TransitionTenant(context.Background(), tenant.ID, Active, 1, "stale request"); err != ErrConflict {
		t.Fatalf("expected conflict, got %v", err)
	}
}

func TestSubscriptionValidation(t *testing.T) {
	if err := ValidateSubscription("02", "growth"); err != nil {
		t.Fatal(err)
	}
	if err := ValidateSubscription("99", "growth"); err == nil {
		t.Fatal("invalid offer accepted")
	}
	if err := ValidateSubscription("02", "free"); err == nil {
		t.Fatal("invalid tier accepted")
	}
}
