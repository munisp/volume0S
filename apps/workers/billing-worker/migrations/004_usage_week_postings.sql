-- Immutable manifests for settled ISO-week usage/cost journal pairs.
-- This is accounting metadata, not a derived financial balance.
CREATE TABLE usage_week_postings (
    event_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    provider text NOT NULL,
    channel text NOT NULL,
    campaign_id text NOT NULL,
    offer text NOT NULL CHECK (offer IN ('01','02','03','04','05','06','07','08')),
    week text NOT NULL,
    first_day date NOT NULL,
    last_day date NOT NULL,
    event_count bigint NOT NULL CHECK (event_count > 0),
    amount_minor numeric(39,0) NOT NULL CHECK (amount_minor > 0),
    provider_cost_minor numeric(39,0) NOT NULL CHECK (provider_cost_minor >= 0),
    currency text NOT NULL CHECK (currency IN ('NGN', 'USD')),
    ledger_transfer_id numeric(39,0) NOT NULL UNIQUE,
    provider_cost_transfer_id numeric(39,0) UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (last_day >= first_day AND last_day - first_day <= 6),
    CHECK ((provider_cost_minor = 0) = (provider_cost_transfer_id IS NULL))
);

CREATE INDEX usage_week_postings_tenant_week_idx
    ON usage_week_postings (tenant_id, week DESC, provider, channel);

CREATE TRIGGER usage_week_postings_append_only
    BEFORE UPDATE OR DELETE ON usage_week_postings
    FOR EACH ROW EXECUTE FUNCTION prevent_billing_document_mutation();

ALTER TABLE usage_week_postings ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_week_postings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON usage_week_postings
    USING (tenant_id = current_setting('volumeos.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('volumeos.tenant_id', true));
