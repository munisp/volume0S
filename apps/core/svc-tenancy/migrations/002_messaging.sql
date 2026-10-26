BEGIN;

CREATE TABLE IF NOT EXISTS suppressions (
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    channel text NOT NULL CHECK (channel IN ('email','sms','whatsapp','social')),
    recipient_digest bytea NOT NULL CHECK (octet_length(recipient_digest) = 32),
    reason text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 500),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, channel, recipient_digest)
);

CREATE TABLE IF NOT EXISTS dispatch_results (
    tenant_id text NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    idempotency_key text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 1 AND 512),
    provider_id text NOT NULL CHECK (char_length(provider_id) BETWEEN 1 AND 512),
    cost_minor bigint NOT NULL CHECK (cost_minor >= 0),
    currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, idempotency_key)
);

ALTER TABLE suppressions ENABLE ROW LEVEL SECURITY;
ALTER TABLE dispatch_results ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON suppressions
    USING (tenant_id = current_setting('volumeos.tenant_id', true));
CREATE POLICY tenant_isolation ON dispatch_results
    USING (tenant_id = current_setting('volumeos.tenant_id', true));

COMMIT;
