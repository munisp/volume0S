package main

import (
	"context"
	_ "embed"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed migrations/001_initial.sql
var initialMigration string

type PostgresStore struct{ pool *pgxpool.Pool }

func NewPostgresStore(ctx context.Context, databaseURL string) (*PostgresStore, error) {
	config, err := pgxpool.ParseConfig(databaseURL)
	if err != nil {
		return nil, fmt.Errorf("parse DATABASE_URL: %w", err)
	}
	config.MaxConns = 20
	config.MinConns = 1
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, fmt.Errorf("connect postgres: %w", err)
	}
	store := &PostgresStore{pool: pool}
	if err := store.migrate(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return store, nil
}

func (s *PostgresStore) migrate(ctx context.Context) error {
	conn, err := s.pool.Acquire(ctx)
	if err != nil {
		return fmt.Errorf("acquire migration connection: %w", err)
	}
	defer conn.Release()
	if _, err := conn.Exec(ctx, "SELECT pg_advisory_lock(hashtext('volumeos.tenancy.migrations'))"); err != nil {
		return err
	}
	defer func() {
		_, _ = conn.Exec(context.Background(), "SELECT pg_advisory_unlock(hashtext('volumeos.tenancy.migrations'))")
	}()
	if _, err := conn.Exec(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		return fmt.Errorf("create migration catalog: %w", err)
	}
	result, err := conn.Exec(ctx, `INSERT INTO schema_migrations(version) VALUES ('001_initial') ON CONFLICT DO NOTHING`)
	if err != nil {
		return fmt.Errorf("record migration: %w", err)
	}
	if result.RowsAffected() == 0 {
		return nil
	}
	if _, err := conn.Exec(ctx, initialMigration); err != nil {
		_, _ = conn.Exec(context.Background(), `DELETE FROM schema_migrations WHERE version='001_initial'`)
		return fmt.Errorf("apply 001_initial migration: %w", err)
	}
	return nil
}

func (s *PostgresStore) Close()                          { s.pool.Close() }
func (s *PostgresStore) Ready(ctx context.Context) error { return s.pool.Ping(ctx) }

func (s *PostgresStore) tx(ctx context.Context, tenantID string, fn func(pgx.Tx) error) error {
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err = tx.Exec(ctx, "SELECT set_config('volumeos.tenant_id', $1, true)", tenantID); err != nil {
		return err
	}
	if err = fn(tx); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func pgError(err error) error {
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) && pgErr.Code == "23505" {
		return ErrConflict
	}
	return err
}

func (s *PostgresStore) CreateTenant(ctx context.Context, t Tenant) error {
	return pgError(s.tx(ctx, t.ID, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, `INSERT INTO tenants (id,name,slug,state,outbound_suspended,version,created_at,updated_at)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8)`, t.ID, t.Name, t.Slug, t.State, t.OutboundSuspended, t.Version, t.CreatedAt, t.UpdatedAt)
		return err
	}))
}

func scanTenant(row pgx.Row) (Tenant, error) {
	var t Tenant
	err := row.Scan(&t.ID, &t.Name, &t.Slug, &t.State, &t.OutboundSuspended, &t.Version, &t.CreatedAt, &t.UpdatedAt)
	return t, pgError(err)
}

const tenantColumns = `id,name,slug,state,outbound_suspended,version,created_at,updated_at`

func (s *PostgresStore) Tenant(ctx context.Context, id string) (Tenant, error) {
	var t Tenant
	err := s.tx(ctx, id, func(tx pgx.Tx) error {
		var err error
		t, err = scanTenant(tx.QueryRow(ctx, "SELECT "+tenantColumns+" FROM tenants WHERE id=$1", id))
		if err != nil {
			return err
		}
		rows, err := tx.Query(ctx, `SELECT id,tenant_id,name,created_at FROM workspaces WHERE tenant_id=$1 ORDER BY created_at`, id)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var w Workspace
			if err := rows.Scan(&w.ID, &w.TenantID, &w.Name, &w.CreatedAt); err != nil {
				return err
			}
			t.Workspaces = append(t.Workspaces, w)
		}
		if err := rows.Err(); err != nil {
			return err
		}
		subs, err := tx.Query(ctx, `SELECT id,tenant_id,offer,tier,created_at FROM subscriptions WHERE tenant_id=$1 ORDER BY created_at`, id)
		if err != nil {
			return err
		}
		defer subs.Close()
		for subs.Next() {
			var sub Subscription
			if err := subs.Scan(&sub.ID, &sub.TenantID, &sub.Offer, &sub.Tier, &sub.CreatedAt); err != nil {
				return err
			}
			t.Subscriptions = append(t.Subscriptions, sub)
		}
		return subs.Err()
	})
	return t, err
}

func (s *PostgresStore) TransitionTenant(ctx context.Context, id string, to Lifecycle, version int64, reason string) (Tenant, error) {
	var result Tenant
	err := s.tx(ctx, id, func(tx pgx.Tx) error {
		current, err := scanTenant(tx.QueryRow(ctx, "SELECT "+tenantColumns+" FROM tenants WHERE id=$1 FOR UPDATE", id))
		if err != nil {
			return err
		}
		if current.Version != version {
			return ErrConflict
		}
		if !CanTransition(current.State, to) {
			return ErrInvalidChange
		}
		result, err = scanTenant(tx.QueryRow(ctx, `UPDATE tenants SET state=$2,version=version+1,updated_at=now()
			WHERE id=$1 AND version=$3 RETURNING `+tenantColumns, id, to, version))
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `INSERT INTO lifecycle_events (tenant_id,event_type,from_state,to_state,reason)
			VALUES ($1,'lifecycle.transition',$2,$3,$4)`, id, current.State, to, strings.TrimSpace(reason))
		return err
	})
	return result, err
}

func (s *PostgresStore) SetOutboundSuspended(ctx context.Context, id string, suspended bool, reason string) (Tenant, error) {
	var result Tenant
	err := s.tx(ctx, id, func(tx pgx.Tx) error {
		current, err := scanTenant(tx.QueryRow(ctx, "SELECT "+tenantColumns+" FROM tenants WHERE id=$1 FOR UPDATE", id))
		if err != nil {
			return err
		}
		if current.State == Offboarded && !suspended {
			return ErrInvalidChange
		}
		result, err = scanTenant(tx.QueryRow(ctx, `UPDATE tenants SET outbound_suspended=$2,version=version+1,updated_at=now()
			WHERE id=$1 RETURNING `+tenantColumns, id, suspended))
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `INSERT INTO lifecycle_events (tenant_id,event_type,to_state,reason)
			VALUES ($1,'outbound.suspension',$2,$3)`, id, current.State, strings.TrimSpace(reason))
		return err
	})
	return result, err
}

func (s *PostgresStore) CreateWorkspace(ctx context.Context, tenantID, name string) (Workspace, error) {
	w := Workspace{ID: newID(), TenantID: tenantID, Name: strings.TrimSpace(name), CreatedAt: time.Now().UTC()}
	if w.Name == "" || len(w.Name) > 120 {
		return w, ErrInvalidChange
	}
	err := s.tx(ctx, tenantID, func(tx pgx.Tx) error {
		var state Lifecycle
		if err := tx.QueryRow(ctx, "SELECT state FROM tenants WHERE id=$1", tenantID).Scan(&state); err != nil {
			return pgError(err)
		}
		if state == Offboarded {
			return ErrInvalidChange
		}
		_, err := tx.Exec(ctx, `INSERT INTO workspaces (id,tenant_id,name,created_at) VALUES ($1,$2,$3,$4)`, w.ID, w.TenantID, w.Name, w.CreatedAt)
		return err
	})
	return w, err
}

func (s *PostgresStore) CreateSubscription(ctx context.Context, tenantID, offer, tier string) (Subscription, error) {
	sub := Subscription{ID: newID(), TenantID: tenantID, Offer: offer, Tier: tier, CreatedAt: time.Now().UTC()}
	if err := ValidateSubscription(offer, tier); err != nil {
		return sub, ErrInvalidChange
	}
	err := s.tx(ctx, tenantID, func(tx pgx.Tx) error {
		var state Lifecycle
		if err := tx.QueryRow(ctx, "SELECT state FROM tenants WHERE id=$1", tenantID).Scan(&state); err != nil {
			return pgError(err)
		}
		if state == Offboarded {
			return ErrInvalidChange
		}
		_, err := tx.Exec(ctx, `INSERT INTO subscriptions (id,tenant_id,offer,tier,created_at) VALUES ($1,$2,$3,$4,$5)`, sub.ID, sub.TenantID, sub.Offer, sub.Tier, sub.CreatedAt)
		return err
	})
	return sub, pgError(err)
}
