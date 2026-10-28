CREATE TABLE IF NOT EXISTS offer_cost_reservations (
    tenant_id text NOT NULL, job_id text NOT NULL, amount_minor bigint NOT NULL CHECK (amount_minor > 0),
    idempotency_key text NOT NULL PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE offer_cost_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE offer_cost_reservations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON offer_cost_reservations USING (tenant_id=current_setting('volumeos.tenant_id',true)) WITH CHECK (tenant_id=current_setting('volumeos.tenant_id',true));
CREATE INDEX IF NOT EXISTS offer_cost_job ON offer_cost_reservations(tenant_id,job_id);
