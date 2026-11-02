-- ============================================================================
-- 0281_drop_integrations_triggers -- no table in the integrations schema carries an audit trigger.
--
-- 0231 attached `audit_row` (audit.record()) to integrations.platforms, platform_features and
-- advisors_map. audit.record() writes an audit.log row whose tenant_id has a foreign key to
-- auth.tenants, but these tables' `tenant_id` is the Intelliflo-side tenant (a vendor id, e.g.
-- 12551), so the insert fails the foreign key and the whole statement with it: the tenant sync's
-- upsert of a platform row fails with `log_tenant_id_fkey`.
--
-- 0279 already drops those three by name. This one drops `audit_row` from EVERY table in the schema,
-- by looking the triggers up, so a database that missed 0279 (or has a table it did not name) ends
-- in the same state. A NEW migration rather than an edit to 0231: 0231 is already applied in dev.
--
-- Idempotent: it finds only triggers that exist, so a database without them passes untouched.
-- audit.attach has no registry beside the trigger itself, so there is nothing else to undo.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.relname
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'integrations'
          AND t.tgname = 'audit_row'
          AND NOT t.tgisinternal
    LOOP
        EXECUTE format('DROP TRIGGER audit_row ON integrations.%I', r.relname);
        RAISE NOTICE 'dropped audit_row from integrations.%', r.relname;
    END LOOP;
END $$;
