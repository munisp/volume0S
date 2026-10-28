CREATE TABLE IF NOT EXISTS leads (
    tenant_id text NOT NULL, lead_id text NOT NULL, dedupe_key text NOT NULL,
    business_name text NOT NULL, phone text, contact_name text, address text, niche text, h3_r9 text,
    consent_reference text, stage text NOT NULL, created_at_unix bigint NOT NULL, updated_at_unix bigint NOT NULL,
    version integer NOT NULL CHECK (version > 0), PRIMARY KEY (tenant_id, lead_id),
    UNIQUE (tenant_id, dedupe_key)
);
CREATE TABLE IF NOT EXISTS lead_sources (
    tenant_id text NOT NULL, source text NOT NULL, source_record_id text NOT NULL,
    lead_id text NOT NULL, fingerprint text NOT NULL, lineage jsonb NOT NULL,
    PRIMARY KEY (tenant_id, source, source_record_id)
);
CREATE TABLE IF NOT EXISTS lead_lineage (
    sequence bigint GENERATED ALWAYS AS IDENTITY, tenant_id text NOT NULL, lead_id text NOT NULL,
    event jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (tenant_id, sequence)
);
CREATE TABLE IF NOT EXISTS lead_scores (
    tenant_id text NOT NULL, lead_id text NOT NULL, model_version text NOT NULL, weight_version text NOT NULL,
    gold_lineage_id text NOT NULL, score double precision NOT NULL, reasons jsonb NOT NULL, created_at_unix bigint NOT NULL,
    PRIMARY KEY (tenant_id, lead_id, model_version, weight_version, created_at_unix)
);
CREATE TABLE IF NOT EXISTS lead_cadence (
    tenant_id text NOT NULL, lead_id text NOT NULL, step text NOT NULL, status text NOT NULL,
    approval_id text, created_at_unix bigint NOT NULL, PRIMARY KEY (tenant_id, lead_id, step)
);
CREATE TABLE IF NOT EXISTS lead_conversions (
    tenant_id text NOT NULL, conversion_id text NOT NULL, lead_id text NOT NULL UNIQUE,
    opportunity_id text NOT NULL, consent_reference text NOT NULL, actor text NOT NULL, created_at_unix bigint NOT NULL,
    PRIMARY KEY (tenant_id, conversion_id)
);
CREATE TABLE IF NOT EXISTS lead_territory_assignments (
    tenant_id text NOT NULL, lead_id text NOT NULL UNIQUE, h3_r9 text NOT NULL, owner_id text NOT NULL,
    model_version text NOT NULL, weight_version text NOT NULL, gold_lineage_id text NOT NULL, assigned_at_unix bigint NOT NULL,
    PRIMARY KEY (tenant_id, lead_id)
);
REVOKE ALL ON leads, lead_sources, lead_lineage, lead_scores, lead_cadence, lead_conversions, lead_territory_assignments FROM PUBLIC;
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['leads','lead_sources','lead_lineage','lead_scores','lead_cadence','lead_conversions','lead_territory_assignments'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=current_setting(''volumeos.tenant_id'',true)) WITH CHECK (tenant_id=current_setting(''volumeos.tenant_id'',true))', table_name);
    END LOOP;
END $$;
CREATE OR REPLACE FUNCTION reject_lead_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'lead lineage is append-only'; END $$;
DROP TRIGGER IF EXISTS lead_lineage_immutable ON lead_lineage;
CREATE TRIGGER lead_lineage_immutable BEFORE UPDATE OR DELETE ON lead_lineage FOR EACH ROW EXECUTE FUNCTION reject_lead_evidence_mutation();
DROP TRIGGER IF EXISTS lead_scores_immutable ON lead_scores;
CREATE TRIGGER lead_scores_immutable BEFORE UPDATE OR DELETE ON lead_scores FOR EACH ROW EXECUTE FUNCTION reject_lead_evidence_mutation();
