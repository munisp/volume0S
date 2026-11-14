package main

import "testing"

func TestShopOwnerHasDeliveryInOwnBusinessOnly(t *testing.T) {
	owner := Claims{Roles: map[string]bool{"client-owner": true}}
	if !owner.Can("delivery") || !owner.Can("reader") {
		t.Fatal("a shop owner runs their own services (delivery) and reads their business")
	}
	for _, role := range []string{"sales", "finance", "analyst", "super-operator"} {
		if owner.Can(role) {
			t.Fatalf("a shop owner must not get %s", role)
		}
	}
}
