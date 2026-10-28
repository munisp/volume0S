-- Billing documents and workflow metadata only. No derived financial balances.
CREATE TABLE billing_sequences (
    tenant_id text NOT NULL,
    fiscal_year smallint NOT NULL,
    kind text NOT NULL CHECK (kind IN ('invoice', 'receipt')),
    next_number bigint NOT NULL CHECK (next_number > 0),
    PRIMARY KEY (tenant_id, fiscal_year, kind)
);

CREATE TABLE invoices (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    subscription_id text NOT NULL,
    cycle text NOT NULL,
    document_number text NOT NULL UNIQUE,
    issue_date date NOT NULL,
    currency text NOT NULL CHECK (currency IN ('NGN', 'USD')),
    subtotal_minor numeric(39,0) NOT NULL,
    tax_minor numeric(39,0) NOT NULL,
    total_minor numeric(39,0) NOT NULL,
    seller_tin text NOT NULL,
    buyer_tin text NOT NULL,
    structured jsonb NOT NULL,
    pdf bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, subscription_id, cycle)
);

CREATE TABLE receipts (
    id text PRIMARY KEY,
    invoice_id text NOT NULL REFERENCES invoices(id),
    tenant_id text NOT NULL,
    document_number text NOT NULL UNIQUE,
    ledger_transfer_id numeric(39,0) NOT NULL UNIQUE,
    amount_minor numeric(39,0) NOT NULL,
    currency text NOT NULL CHECK (currency IN ('NGN', 'USD')),
    structured jsonb NOT NULL,
    pdf bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE usage_rollups (
    event_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    channel text NOT NULL,
    campaign_id text NOT NULL,
    day date NOT NULL,
    event_count bigint NOT NULL CHECK (event_count > 0),
    amount_minor numeric(39,0) NOT NULL,
    currency text NOT NULL CHECK (currency IN ('NGN', 'USD')),
    ledger_transfer_id numeric(39,0) NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dunning_cases (
    tenant_id text NOT NULL,
    subscription_id text NOT NULL,
    cycle text NOT NULL,
    state text NOT NULL CHECK (state IN ('nudging', 'paused', 'recovered')),
    attempts integer NOT NULL CHECK (attempts >= 0),
    recovery_reference text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subscription_id, cycle)
);

CREATE TABLE fx_closes (
    event_id text PRIMARY KEY,
    tenant_id text NOT NULL CHECK (tenant_id = 'platform'),
    provider text NOT NULL,
    month text NOT NULL,
    usd_minor numeric(39,0) NOT NULL,
    previous_ngn_minor_per_usd numeric(39,0) NOT NULL,
    closing_ngn_minor_per_usd numeric(39,0) NOT NULL,
    revaluation_minor numeric(39,0) NOT NULL,
    direction text NOT NULL CHECK (direction IN ('gain', 'loss')),
    ledger_transfer_id numeric(39,0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, month)
);

CREATE INDEX invoices_tenant_issue_idx ON invoices (tenant_id, issue_date DESC);
CREATE INDEX receipts_tenant_created_idx ON receipts (tenant_id, created_at DESC);
CREATE INDEX usage_rollups_tenant_day_idx ON usage_rollups (tenant_id, day DESC);
CREATE INDEX dunning_state_idx ON dunning_cases (state, updated_at);

CREATE FUNCTION prevent_billing_document_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'billing documents and postings are append-only';
END;
$$;

CREATE TRIGGER invoices_append_only BEFORE UPDATE OR DELETE ON invoices
    FOR EACH ROW EXECUTE FUNCTION prevent_billing_document_mutation();
CREATE TRIGGER receipts_append_only BEFORE UPDATE OR DELETE ON receipts
    FOR EACH ROW EXECUTE FUNCTION prevent_billing_document_mutation();
CREATE TRIGGER usage_rollups_append_only BEFORE UPDATE OR DELETE ON usage_rollups
    FOR EACH ROW EXECUTE FUNCTION prevent_billing_document_mutation();
CREATE TRIGGER fx_closes_append_only BEFORE UPDATE OR DELETE ON fx_closes
    FOR EACH ROW EXECUTE FUNCTION prevent_billing_document_mutation();

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'billing_sequences', 'invoices', 'receipts', 'usage_rollups', 'dunning_cases', 'fx_closes'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''volumeos.tenant_id'', true)) WITH CHECK (tenant_id = current_setting(''volumeos.tenant_id'', true))',
            table_name
        );
    END LOOP;
END;
$$;
