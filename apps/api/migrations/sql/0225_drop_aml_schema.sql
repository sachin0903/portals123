-- ============================================================================
-- 0225_drop_aml_schema.sql — drop the Creditsafe mirror (schema aml).
--
-- ComplyCube is the one screening provider (0221) and nothing reads or writes
-- schema aml any more. Its rows are regulated screening records, kept for five
-- years, so this migration REFUSES to run while any aml.* table holds a row:
-- it raises, the whole upgrade rolls back and the deploy stops. An operator
-- then exports schema aml to object storage, empties its tables and deploys
-- again. A database with no schema aml passes.
--
-- aml.sync_state is not counted. It is the sync engine's own singleton (0037
-- seeds its one row in every database): watermarks and counts, not records of
-- a screen. Every other aml.* table is counted, the operator's stored
-- Creditsafe login (0038) included.
--
-- The guard counts as service_role. Every aml.* table is FORCE ROW LEVEL
-- SECURITY and the migrator is not BYPASSRLS, so a count run as the migrator
-- sees zero rows and would pass every time (docs/gotchas.md, the RLS no-op).
--
-- audit.log keeps the rows the aml.* audit trigger wrote. The firm branch of
-- audit_log_read still excludes aml.% (0221), so they stay withheld from a
-- firm. Nothing else in the database depends on schema aml.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

SET LOCAL ROLE service_role;

DO $$
DECLARE
  tbl text;
  n bigint;
  held text[] := '{}';
BEGIN
  FOR tbl IN
    SELECT c.relname
      FROM pg_class c JOIN pg_namespace ns ON ns.oid = c.relnamespace
     WHERE ns.nspname = 'aml' AND c.relkind IN ('r', 'p') AND c.relname <> 'sync_state'
     ORDER BY c.relname
  LOOP
    EXECUTE format('SELECT count(*) FROM aml.%I', tbl) INTO n;
    IF n > 0 THEN
      held := held || format('aml.%s (%s rows)', tbl, n);
    END IF;
  END LOOP;
  IF cardinality(held) > 0 THEN
    RAISE EXCEPTION 'Refusing to drop schema aml: % still hold regulated screening records.',
      array_to_string(held, ', ')
      USING HINT = 'Export every aml.* table to object storage, where it is kept for five '
                   'years. Then empty the aml.* tables and deploy again. This migration '
                   'changed nothing.';
  END IF;
END $$;

RESET ROLE;

DROP SCHEMA IF EXISTS aml CASCADE;
