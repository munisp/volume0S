CREATE TABLE IF NOT EXISTS consents (
    tenant_id text NOT NULL,
    reference text NOT NULL,
    subject_digest text NOT NULL,
    purpose text NOT NULL,
    lawful_basis text NOT NULL,
    status text NOT NULL CHECK (status IN ('granted','withdrawn')),
    version integer NOT NULL CHECK (version > 0),
    actor text NOT NULL,
    created_at_unix bigint NOT NULL,
    PRIMARY KEY (tenant_id, reference)
);
CREATE TABLE IF NOT EXISTS consent_history (
    tenant_id text NOT NULL,
    reference text NOT NULL,
    subject_digest text NOT NULL,
    purpose text NOT NULL,
    lawful_basis text NOT NULL,
    status text NOT NULL,
    version integer NOT NULL,
    actor text NOT NULL,
    created_at_unix bigint NOT NULL,
    PRIMARY KEY (tenant_id, reference, version)
);
CREATE TABLE IF NOT EXISTS dsar_requests (
    tenant_id text NOT NULL,
    request_id text NOT NULL,
    subject_digest text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('export','delete')),
    status text NOT NULL,
    actor text NOT NULL,
    created_at_unix bigint NOT NULL,
    export_manifest jsonb,
    deletion jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (tenant_id, request_id)
);
CREATE TABLE IF NOT EXISTS privacy_audit (
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    tenant_id text NOT NULL,
    event jsonb NOT NULL,
    previous_sha256 text NOT NULL,
    event_sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, sequence)
);
REVOKE ALL ON consents, consent_history, dsar_requests, privacy_audit FROM PUBLIC;
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['consents','consent_history','dsar_requests','privacy_audit'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=current_setting(''volumeos.tenant_id'',true)) WITH CHECK (tenant_id=current_setting(''volumeos.tenant_id'',true))', table_name);
    END LOOP;
END $$;
CREATE OR REPLACE FUNCTION reject_privacy_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'privacy evidence is append-only'; END $$;
DROP TRIGGER IF EXISTS consent_history_immutable ON consent_history;
CREATE TRIGGER consent_history_immutable BEFORE UPDATE OR DELETE ON consent_history FOR EACH ROW EXECUTE FUNCTION reject_privacy_mutation();
DROP TRIGGER IF EXISTS privacy_audit_immutable ON privacy_audit;
CREATE TRIGGER privacy_audit_immutable BEFORE UPDATE OR DELETE ON privacy_audit FOR EACH ROW EXECUTE FUNCTION reject_privacy_mutation();
