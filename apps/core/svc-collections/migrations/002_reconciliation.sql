-- Platform-scoped review outcomes. Original settled receipts remain immutable.
CREATE TABLE collection_resolutions (
    resolution_id text PRIMARY KEY,
    event_id text NOT NULL REFERENCES collection_receipts(event_id),
    provider text NOT NULL,
    reference text NOT NULL,
    tenant_id text NOT NULL CHECK (tenant_id = 'platform'),
    credited_tenant_id text NOT NULL,
    amount_minor text NOT NULL,
    currency text NOT NULL CHECK (currency IN ('NGN', 'USD')),
    ledger_transfer_id text NOT NULL UNIQUE,
    approved_by text NOT NULL,
    reason text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, reference)
);

CREATE INDEX collection_resolutions_tenant_created_idx
    ON collection_resolutions (credited_tenant_id, created_at DESC);
CREATE INDEX collection_suspense_open_idx
    ON collection_receipts (created_at) WHERE disposition = 'suspense';

CREATE TRIGGER collection_resolutions_append_only
    BEFORE UPDATE OR DELETE ON collection_resolutions
    FOR EACH ROW EXECUTE FUNCTION prevent_collection_receipt_mutation();

ALTER TABLE collection_resolutions ENABLE ROW LEVEL SECURITY;
ALTER TABLE collection_resolutions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON collection_resolutions
    USING (tenant_id = current_setting('volumeos.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('volumeos.tenant_id', true));
