-- Provider acceptance is an append-only delivery fact, not a device-delivery claim.
CREATE TABLE document_deliveries (
    id text PRIMARY KEY,
    tenant_id text NOT NULL,
    document_kind text NOT NULL CHECK (document_kind IN ('invoice','receipt')),
    document_id text NOT NULL,
    channel text NOT NULL CHECK (channel = 'whatsapp'),
    recipient text NOT NULL,
    provider_message_id text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status = 'provider_accepted'),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, document_kind, document_id, channel, recipient)
);

CREATE INDEX document_deliveries_tenant_created_idx ON document_deliveries (tenant_id, created_at DESC);

CREATE TRIGGER document_deliveries_append_only BEFORE UPDATE OR DELETE ON document_deliveries
    FOR EACH ROW EXECUTE FUNCTION prevent_billing_document_mutation();

ALTER TABLE document_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_deliveries FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON document_deliveries
    USING (tenant_id = current_setting('volumeos.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('volumeos.tenant_id', true));
