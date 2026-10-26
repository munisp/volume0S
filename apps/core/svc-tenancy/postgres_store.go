package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	_ "embed"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed migrations/001_initial.sql
var initialMigration string

//go:embed migrations/002_messaging.sql
var messagingMigration string

type PostgresStore struct {
	pool           *pgxpool.Pool
	suppressionKey []byte
}

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
	suppressionKey := []byte(os.Getenv("SUPPRESSION_HMAC_SECRET"))
	if len(suppressionKey) < 32 {
		pool.Close()
		return nil, errors.New("SUPPRESSION_HMAC_SECRET must contain at least 32 bytes")
	}
	store := &PostgresStore{pool: pool, suppressionKey: suppressionKey}
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
	migrations := []struct {
		version string
		sql     string
	}{{"001_initial", initialMigration}, {"002_messaging", messagingMigration}}
	for _, migration := range migrations {
		result, err := conn.Exec(ctx, `INSERT INTO schema_migrations(version) VALUES ($1) ON CONFLICT DO NOTHING`, migration.version)
		if err != nil {
			return fmt.Errorf("record migration %s: %w", migration.version, err)
		}
		if result.RowsAffected() == 0 {
			continue
		}
		if _, err := conn.Exec(ctx, migration.sql); err != nil {
			_, _ = conn.Exec(context.Background(), `DELETE FROM schema_migrations WHERE version=$1`, migration.version)
			return fmt.Errorf("apply %s migration: %w", migration.version, err)
		}
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
		if err := tx.QueryRow(ctx, `SELECT state FROM tenants WHERE id=$1`, tenantID).Scan(&state); err != nil {
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
		if err := tx.QueryRow(ctx, `SELECT state FROM tenants WHERE id=$1`, tenantID).Scan(&state); err != nil {
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

func (s *PostgresStore) recipientDigest(recipient string) []byte {
	mac := hmac.New(sha256.New, s.suppressionKey)
	_, _ = mac.Write([]byte(normalizeRecipient(recipient)))
	return mac.Sum(nil)
}

func (s *PostgresStore) AuthorizeDispatch(ctx context.Context, tenantID, channel, recipient string) (DispatchDecision, error) {
	decision := DispatchDecision{}
	if !validMessagingChannel(channel) || (channel != "social" && normalizeRecipient(recipient) == "") {
		return decision, ErrInvalidChange
	}
	err := s.tx(ctx, tenantID, func(tx pgx.Tx) error {
		var state Lifecycle
		var suspended bool
		if err := tx.QueryRow(ctx, `SELECT state,outbound_suspended FROM tenants WHERE id=$1`, tenantID).Scan(&state, &suspended); err != nil {
			return err
		}
		switch {
		case state != Active:
			decision.Reason = "tenant_not_active"
		case suspended:
			decision.Reason = "tenant_suspended"
		case channel != "social":
			var exists bool
			if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM suppressions WHERE tenant_id=$1 AND channel=$2 AND recipient_digest=$3)`,
				tenantID, channel, s.recipientDigest(recipient)).Scan(&exists); err != nil {
				return err
			}
			if exists {
				decision.Reason = "recipient_suppressed"
			}
		}
		if decision.Reason == "" {
			decision.Allowed, decision.Reason = true, "authorized"
		}
		return nil
	})
	return decision, pgError(err)
}

func (s *PostgresStore) SetSuppression(ctx context.Context, tenantID, channel, recipient string, suppressed bool, reason string) error {
	reason = strings.TrimSpace(reason)
	if !validSuppressionInput(channel, recipient, reason, suppressed) {
		return ErrInvalidChange
	}
	return pgError(s.tx(ctx, tenantID, func(tx pgx.Tx) error {
		var exists bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM tenants WHERE id=$1)`, tenantID).Scan(&exists); err != nil {
			return err
		}
		if !exists {
			return ErrNotFound
		}
		if suppressed {
			_, err := tx.Exec(ctx, `INSERT INTO suppressions(tenant_id,channel,recipient_digest,reason) VALUES ($1,$2,$3,$4)
				ON CONFLICT (tenant_id,channel,recipient_digest) DO UPDATE SET reason=EXCLUDED.reason,created_at=now()`,
				tenantID, channel, s.recipientDigest(recipient), reason)
			return err
		}
		_, err := tx.Exec(ctx, `DELETE FROM suppressions WHERE tenant_id=$1 AND channel=$2 AND recipient_digest=$3`,
			tenantID, channel, s.recipientDigest(recipient))
		return err
	}))
}

func (s *PostgresStore) GetDispatchResult(ctx context.Context, tenantID, key string) (DispatchResult, error) {
	var result DispatchResult
	if key == "" || len(key) > 512 {
		return result, ErrInvalidChange
	}
	err := s.tx(ctx, tenantID, func(tx pgx.Tx) error {
		return tx.QueryRow(ctx, `SELECT idempotency_key,tenant_id,provider_id,cost_minor,currency,recorded_at
			FROM dispatch_results WHERE tenant_id=$1 AND idempotency_key=$2`, tenantID, key).Scan(
			&result.Key, &result.TenantID, &result.ProviderID, &result.CostMinor, &result.Currency, &result.RecordedAt)
	})
	return result, pgError(err)
}

func (s *PostgresStore) PutDispatchResult(ctx context.Context, value DispatchResult) (DispatchResult, error) {
	var result DispatchResult
	if value.Key == "" || len(value.Key) > 512 || !tenantIDPattern.MatchString(value.TenantID) ||
		strings.TrimSpace(value.ProviderID) == "" || len(value.ProviderID) > 512 || value.CostMinor < 0 || !validCurrency(value.Currency) {
		return result, ErrInvalidChange
	}
	err := s.tx(ctx, value.TenantID, func(tx pgx.Tx) error {
		var exists bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM tenants WHERE id=$1)`, value.TenantID).Scan(&exists); err != nil {
			return err
		}
		if !exists {
			return ErrNotFound
		}
		if _, err := tx.Exec(ctx, `INSERT INTO dispatch_results(tenant_id,idempotency_key,provider_id,cost_minor,currency)
			VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING`, value.TenantID, value.Key, value.ProviderID, value.CostMinor, value.Currency); err != nil {
			return err
		}
		if err := tx.QueryRow(ctx, `SELECT idempotency_key,tenant_id,provider_id,cost_minor,currency,recorded_at
			FROM dispatch_results WHERE tenant_id=$1 AND idempotency_key=$2`, value.TenantID, value.Key).Scan(
			&result.Key, &result.TenantID, &result.ProviderID, &result.CostMinor, &result.Currency, &result.RecordedAt); err != nil {
			return err
		}
		if result.ProviderID != value.ProviderID || result.CostMinor != value.CostMinor || result.Currency != value.Currency {
			return ErrConflict
		}
		return nil
	})
	return result, pgError(err)
}
