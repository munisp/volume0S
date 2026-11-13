BEGIN;

-- The service connects as the owner of these tables, and Postgres skips row-level security for a table's owner
-- unless it is forced. Before this, tenant isolation here rested on WHERE clauses alone; now the database refuses
-- another business's rows even if a query forgets its tenant filter.
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
ALTER TABLE workspaces FORCE ROW LEVEL SECURITY;
ALTER TABLE subscriptions FORCE ROW LEVEL SECURITY;
ALTER TABLE lifecycle_events FORCE ROW LEVEL SECURITY;
ALTER TABLE suppressions FORCE ROW LEVEL SECURITY;
ALTER TABLE dispatch_results FORCE ROW LEVEL SECURITY;

-- Platform operators' client list (ListTenants, the only cross-business query) reads as tenant 'platform'.
-- Read-only: a platform session can't write any business's rows through these.
DROP POLICY IF EXISTS platform_read ON tenants;
DROP POLICY IF EXISTS platform_read ON workspaces;
DROP POLICY IF EXISTS platform_read ON subscriptions;
CREATE POLICY platform_read ON tenants FOR SELECT USING (current_setting('volumeos.tenant_id', true) = 'platform');
CREATE POLICY platform_read ON workspaces FOR SELECT USING (current_setting('volumeos.tenant_id', true) = 'platform');
CREATE POLICY platform_read ON subscriptions FOR SELECT USING (current_setting('volumeos.tenant_id', true) = 'platform');

COMMIT;
