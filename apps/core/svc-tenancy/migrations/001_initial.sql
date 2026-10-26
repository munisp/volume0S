BEGIN;

CREATE TABLE IF NOT EXISTS tenants (
    id text PRIMARY KEY,
    name text NOT NULL CHECK (char_length(name) BETWEEN 2 AND 120),
    slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    state text NOT NULL DEFAULT 'prospect' CHECK (state IN ('prospect','onboarding','active','paused','offboarded')),
    outbound_suspended boolean NOT NULL DEFAULT false,
    version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspaces (
    id uuid PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id uuid PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    offer text NOT NULL CHECK (offer IN ('01','02','03','04','05','06','07','08')),
    tier text NOT NULL CHECK (tier IN ('starter','growth','pro')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, offer)
);

CREATE TABLE IF NOT EXISTS lifecycle_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    event_type text NOT NULL,
    from_state text,
    to_state text,
    reason text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE lifecycle_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON tenants USING (id = current_setting('volumeos.tenant_id', true));
CREATE POLICY tenant_isolation ON workspaces USING (tenant_id = current_setting('volumeos.tenant_id', true));
CREATE POLICY tenant_isolation ON subscriptions USING (tenant_id = current_setting('volumeos.tenant_id', true));
CREATE POLICY tenant_isolation ON lifecycle_events USING (tenant_id = current_setting('volumeos.tenant_id', true));

COMMIT;
