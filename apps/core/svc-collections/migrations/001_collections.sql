-- Payment expectations and immutable receipts. No financial balances are stored here.
CREATE TABLE collection_routes (
    provider text NOT NULL,
    reference text NOT NULL,
    tenant_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, reference)
);

CREATE TABLE collection_expectations (
    provider text NOT NULL,
    reference text NOT NULL,
    tenant_id text NOT NULL,
    amount_minor text NOT NULL,
    currency text NOT NULL CHECK (currency IN ('NGN', 'USD')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, reference)
);

CREATE TABLE collection_receipts (
    event_id text PRIMARY KEY,
    provider text NOT NULL,
    reference text NOT NULL,
    tenant_id text NOT NULL,
    disposition text NOT NULL,
    provider_payment_id text NOT NULL,
    amount_minor text NOT NULL,
    currency text NOT NULL CHECK (currency IN ('NGN', 'USD')),
    ledger_transfer_id text,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, reference)
);

CREATE INDEX collection_receipts_tenant_created_idx ON collection_receipts (tenant_id, created_at DESC);

CREATE FUNCTION prevent_collection_receipt_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'collection receipts and expectations are append-only';
END;
$$;

CREATE TRIGGER collection_receipts_append_only BEFORE UPDATE OR DELETE ON collection_receipts
    FOR EACH ROW EXECUTE FUNCTION prevent_collection_receipt_mutation();
CREATE TRIGGER collection_expectations_append_only BEFORE UPDATE OR DELETE ON collection_expectations
    FOR EACH ROW EXECUTE FUNCTION prevent_collection_receipt_mutation();
CREATE TRIGGER collection_routes_append_only BEFORE UPDATE OR DELETE ON collection_routes
    FOR EACH ROW EXECUTE FUNCTION prevent_collection_receipt_mutation();

ALTER TABLE collection_expectations ENABLE ROW LEVEL SECURITY;
ALTER TABLE collection_expectations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON collection_expectations
    USING (tenant_id = current_setting('volumeos.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('volumeos.tenant_id', true));
ALTER TABLE collection_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE collection_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON collection_receipts
    USING (tenant_id = current_setting('volumeos.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('volumeos.tenant_id', true));
