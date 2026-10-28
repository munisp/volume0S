CREATE TABLE IF NOT EXISTS conversation_channels (
    tenant_id text NOT NULL,
    phone_number_id text NOT NULL,
    created_at_unix bigint NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, phone_number_id)
);
CREATE TABLE IF NOT EXISTS conversations (
    tenant_id text NOT NULL,
    conversation_id text NOT NULL,
    phone_number_id text NOT NULL,
    recipient text NOT NULL,
    contact_name text NOT NULL,
    subject_digest text NOT NULL,
    status text NOT NULL CHECK (status IN ('active','suppressed')),
    created_at_unix bigint NOT NULL,
    PRIMARY KEY (tenant_id, conversation_id)
);
CREATE TABLE IF NOT EXISTS conversation_messages (
    tenant_id text NOT NULL,
    conversation_id text NOT NULL,
    message_id text NOT NULL,
    direction text NOT NULL CHECK (direction IN ('inbound','outbound')),
    recipient text NOT NULL,
    body text NOT NULL,
    fingerprint text NOT NULL,
    created_at_unix bigint NOT NULL,
    provider_message_id text,
    draft_sha256 text,
    PRIMARY KEY (tenant_id, message_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS conversation_outbound_draft_unique
    ON conversation_messages(tenant_id, conversation_id, draft_sha256) WHERE direction = 'outbound';
CREATE TABLE IF NOT EXISTS conversation_drafts (
    tenant_id text NOT NULL,
    conversation_id text NOT NULL,
    draft_sha256 text NOT NULL,
    inbound_message_id text NOT NULL,
    response_text text NOT NULL,
    confidence double precision NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    model_version text NOT NULL,
    escalation_reason text,
    status text NOT NULL CHECK (status IN ('requires-review','approved','rejected','sent')),
    created_at_unix bigint NOT NULL,
    PRIMARY KEY (tenant_id, draft_sha256),
    UNIQUE (tenant_id, conversation_id, inbound_message_id)
);
CREATE TABLE IF NOT EXISTS conversation_approvals (
    tenant_id text NOT NULL,
    conversation_id text NOT NULL,
    draft_sha256 text NOT NULL,
    reviewer text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved','rejected')),
    reason text NOT NULL,
    reviewed_at_unix bigint NOT NULL,
    PRIMARY KEY (tenant_id, draft_sha256, reviewer)
);
CREATE TABLE IF NOT EXISTS conversation_bookings (
    tenant_id text NOT NULL,
    conversation_id text NOT NULL,
    slot_id text NOT NULL,
    hold_id text NOT NULL,
    result jsonb NOT NULL,
    created_at_unix bigint NOT NULL,
    PRIMARY KEY (tenant_id, conversation_id, slot_id)
);
CREATE TABLE IF NOT EXISTS conversation_suppressions (
    tenant_id text NOT NULL,
    recipient text NOT NULL,
    reason text NOT NULL,
    actor text NOT NULL,
    created_at_unix bigint NOT NULL,
    PRIMARY KEY (tenant_id, recipient)
);
REVOKE ALL ON conversation_channels, conversations, conversation_messages, conversation_drafts,
    conversation_approvals, conversation_bookings, conversation_suppressions FROM PUBLIC;
DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['conversation_channels','conversations','conversation_messages',
        'conversation_drafts','conversation_approvals','conversation_bookings','conversation_suppressions'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
        EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=current_setting(''volumeos.tenant_id'',true)) WITH CHECK (tenant_id=current_setting(''volumeos.tenant_id'',true))', table_name);
    END LOOP;
END $$;
CREATE OR REPLACE FUNCTION reject_conversation_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'conversation evidence is append-only'; END $$;
DROP TRIGGER IF EXISTS conversation_messages_immutable ON conversation_messages;
CREATE TRIGGER conversation_messages_immutable BEFORE UPDATE OR DELETE ON conversation_messages FOR EACH ROW EXECUTE FUNCTION reject_conversation_evidence_mutation();
DROP TRIGGER IF EXISTS conversation_approvals_immutable ON conversation_approvals;
CREATE TRIGGER conversation_approvals_immutable BEFORE UPDATE OR DELETE ON conversation_approvals FOR EACH ROW EXECUTE FUNCTION reject_conversation_evidence_mutation();
DROP TRIGGER IF EXISTS conversation_bookings_immutable ON conversation_bookings;
CREATE TRIGGER conversation_bookings_immutable BEFORE UPDATE OR DELETE ON conversation_bookings FOR EACH ROW EXECUTE FUNCTION reject_conversation_evidence_mutation();
DROP TRIGGER IF EXISTS conversation_suppressions_immutable ON conversation_suppressions;
CREATE TRIGGER conversation_suppressions_immutable BEFORE UPDATE OR DELETE ON conversation_suppressions FOR EACH ROW EXECUTE FUNCTION reject_conversation_evidence_mutation();
