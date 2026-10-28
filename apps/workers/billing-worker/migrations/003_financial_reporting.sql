-- Immutable commercial terms used by partner-share statements. No balances.
CREATE TABLE partner_share_contracts (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    partner_id text NOT NULL,
    offer text NOT NULL CHECK (offer IN ('01','02','03','04','05','06','07','08')),
    basis_points integer NOT NULL CHECK (basis_points BETWEEN 0 AND 10000),
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);

CREATE INDEX partner_share_terms_idx ON partner_share_contracts
    (tenant_id, partner_id, offer, effective_from DESC, created_at DESC);

CREATE TRIGGER partner_share_contracts_append_only
    BEFORE UPDATE OR DELETE ON partner_share_contracts
    FOR EACH ROW EXECUTE FUNCTION prevent_billing_document_mutation();

ALTER TABLE partner_share_contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_share_contracts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON partner_share_contracts
    USING (tenant_id = current_setting('volumeos.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('volumeos.tenant_id', true));
