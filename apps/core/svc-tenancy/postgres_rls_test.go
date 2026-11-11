package main

// Row-level security, against a real Postgres. Runs only when TENANCY_TEST_DATABASE_URL points at a database whose
// user OWNS the tables and is not a superuser (as in production: the owner is exactly who FORCE must still restrict).
//
//	docker run -d --name pg-rls -e POSTGRES_PASSWORD=pw -p 55432:5432 postgres:16
//	psql postgres://postgres:pw@localhost:55432/postgres -c "CREATE ROLE app LOGIN PASSWORD 'pw'" -c "CREATE DATABASE tenancy OWNER app"
//	TENANCY_TEST_DATABASE_URL=postgres://app:pw@localhost:55432/tenancy go test -run RLS ./...

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func rlsStore(t *testing.T) *PostgresStore {
	t.Helper()
	url := os.Getenv("TENANCY_TEST_DATABASE_URL")
	if url == "" {
		t.Skip("TENANCY_TEST_DATABASE_URL not set")
	}
	t.Setenv("SUPPRESSION_HMAC_SECRET", strings.Repeat("k", 32))
	ctx := context.Background()
	store, err := NewPostgresStore(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(store.Close)
	var super, bypass bool
	if err := store.pool.QueryRow(ctx, "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").Scan(&super, &bypass); err != nil {
		t.Fatal(err)
	}
	if super || bypass {
		t.Fatal("connect as a plain table owner: a superuser or BYPASSRLS role skips row-level security entirely")
	}
	return store
}

func TestRLSKeepsEachBusinessToItsOwnRows(t *testing.T) {
	store := rlsStore(t)
	ctx := context.Background()
	stamp := time.Now().Format("150405.000000")
	a, b := "rls-a-"+strings.ReplaceAll(stamp, ".", ""), "rls-b-"+strings.ReplaceAll(stamp, ".", "")
	for _, id := range []string{a, b} {
		now := time.Now().UTC()
		if err := store.CreateTenant(ctx, Tenant{ID: id, Name: "RLS " + id, Slug: id, State: "prospect", Version: 1, CreatedAt: now, UpdatedAt: now}); err != nil {
			t.Fatalf("create %s: %v", id, err)
		}
		if _, err := store.CreateWorkspace(ctx, id, "ws"); err != nil {
			t.Fatalf("workspace %s: %v", id, err)
		}
		if _, err := store.CreateSubscription(ctx, id, "01", "starter"); err != nil {
			t.Fatalf("subscription %s: %v", id, err)
		}
		if _, err := store.SetOutboundSuspended(ctx, id, true, "rls test"); err != nil {
			t.Fatalf("suspend %s: %v", id, err)
		}
	}

	// A query that forgets its tenant filter, run in business A's session, still sees only A.
	for _, table := range []string{"tenants", "workspaces", "subscriptions", "lifecycle_events"} {
		column := "tenant_id"
		if table == "tenants" {
			column = "id"
		}
		var others int
		err := store.tx(ctx, a, func(tx pgx.Tx) error {
			return tx.QueryRow(ctx, "SELECT count(*) FROM "+table+" WHERE "+column+" <> $1", a).Scan(&others)
		})
		if err != nil || others != 0 {
			t.Fatalf("%s: business A's session sees %d rows of other businesses (err %v)", table, others, err)
		}
	}
	// Nor can it change another business's rows, or write rows in another business's name.
	err := store.tx(ctx, a, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, "UPDATE tenants SET name = 'hijacked' WHERE id = $1", b)
		if err == nil && tag.RowsAffected() != 0 {
			t.Errorf("A renamed B")
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	err = store.tx(ctx, a, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, "INSERT INTO workspaces (id, tenant_id, name) VALUES (gen_random_uuid(), $1, 'planted')", b)
		return err
	})
	if err == nil {
		t.Fatal("A wrote a workspace into B")
	}
	// A session with no business set sees nothing at all.
	var visible int
	if err := store.pool.QueryRow(ctx, "SELECT count(*) FROM tenants").Scan(&visible); err != nil || visible != 0 {
		t.Fatalf("no tenant set: %d tenants visible (err %v)", visible, err)
	}

	// The platform client list still reads every business, with its workspaces and offers...
	list, err := store.ListTenants(ctx)
	if err != nil {
		t.Fatal(err)
	}
	found := 0
	for _, row := range list {
		if (row.ID == a || row.ID == b) && row.Workspaces == 1 && len(row.Offers) == 1 {
			found++
		}
	}
	if found != 2 {
		t.Fatalf("platform list found %d of the 2 businesses fully: %+v", found, list)
	}
	// ...but only reads: a platform session can't change a business's rows directly.
	err = store.tx(ctx, PlatformTenant, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, "UPDATE tenants SET name = 'platform edit' WHERE id = $1", a)
		if err == nil && tag.RowsAffected() != 0 {
			t.Errorf("a platform session updated a business row")
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	// And each business still reads itself normally.
	got, err := store.Tenant(ctx, a)
	if err != nil || got.ID != a || len(got.Workspaces) != 1 || len(got.Subscriptions) != 1 || !got.OutboundSuspended {
		t.Fatalf("A reads itself: %+v %v", got, err)
	}
}
