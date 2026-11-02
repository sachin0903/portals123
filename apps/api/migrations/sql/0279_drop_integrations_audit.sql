-- ============================================================================
-- 0279_drop_integrations_audit -- the integrations tables carry no audit triggers.
--
-- 0231 attached `audit_row` (audit.record()) to integrations.platforms, platform_features and
-- advisors_map. audit.record() takes the tenant for its row from the row itself, and these tables'
-- `tenant_id` is the Intelliflo-side tenant (a vendor id, not an auth.tenants id), so an audit
-- insert for one of them fails audit.log's tenant foreign key as soon as a platform is updated or
-- deleted.
--
-- A NEW migration rather than an edit to 0231: 0231 is on main and already applied in dev, so an
-- edit would only change databases created from now on and let the rest drift.
--
-- Idempotent: IF EXISTS, so a database that never had the triggers (or already lost them) passes.
-- audit.attach has no registry beside the trigger itself, so there is nothing else to undo.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

DROP TRIGGER IF EXISTS audit_row ON integrations.platforms;
DROP TRIGGER IF EXISTS audit_row ON integrations.platform_features;
DROP TRIGGER IF EXISTS audit_row ON integrations.advisors_map;
