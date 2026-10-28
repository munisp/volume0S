-- Read-only platform reconciliation scope. Tenant writes remain restricted by
-- the original tenant_isolation policy; this policy is SELECT-only.
CREATE POLICY platform_collection_review ON collection_receipts
    FOR SELECT
    USING (current_setting('volumeos.tenant_id', true) = 'platform');
