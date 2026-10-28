CREATE TABLE IF NOT EXISTS census_uploads (
    tenant_id text NOT NULL,
    upload_id text NOT NULL,
    event_id text NOT NULL,
    device_id text NOT NULL,
    manifest jsonb NOT NULL,
    published_document jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    PRIMARY KEY (tenant_id, upload_id),
    UNIQUE (tenant_id, event_id),
    CHECK (length(tenant_id) BETWEEN 1 AND 128),
    CHECK (length(upload_id) BETWEEN 1 AND 128),
    CHECK (event_id ~ '^[0-7][0-9A-HJKMNP-TV-Z]{25}$'),
    CHECK ((published_document IS NULL) = (published_at IS NULL))
);

CREATE TABLE IF NOT EXISTS census_chunks (
    tenant_id text NOT NULL,
    upload_id text NOT NULL,
    chunk_index integer NOT NULL,
    sha256 text NOT NULL,
    content bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, upload_id, chunk_index),
    FOREIGN KEY (tenant_id, upload_id) REFERENCES census_uploads(tenant_id, upload_id) ON DELETE CASCADE,
    CHECK (chunk_index BETWEEN 0 AND 63),
    CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (octet_length(content) BETWEEN 1 AND 262144)
);

ALTER TABLE census_uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE census_uploads FORCE ROW LEVEL SECURITY;
ALTER TABLE census_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE census_chunks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS census_uploads_tenant ON census_uploads;
CREATE POLICY census_uploads_tenant ON census_uploads
    USING (tenant_id = current_setting('volumeos.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('volumeos.tenant_id', true));
DROP POLICY IF EXISTS census_chunks_tenant ON census_chunks;
CREATE POLICY census_chunks_tenant ON census_chunks
    USING (tenant_id = current_setting('volumeos.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('volumeos.tenant_id', true));
