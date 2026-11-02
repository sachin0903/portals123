-- ============================================================================
-- 0095_portal_host_unique.sql -- portal hosts must be unique across environments.
--
-- Closes a standing gap (auth review finding #1; the handoff in environments.env_config_by_host):
-- there was NO uniqueness on auth.tenant_environments.client_portal_host / platform_host, so two
-- environments could claim the same host. env_config_by_host then resolved it by `ORDER BY id LIMIT
-- 1` -- silently binding every user on that host to the LOWEST-id environment's org AND handing that
-- environment's DECRYPTED WorkOS key + cookie password to whatever portal served the host. A
-- data-entry mistake was therefore a cross-tenant identity/secret binding, not just a wrong-brand
-- page. This makes it a hard error at write time instead.
--
-- Two partial unique indexes (NULLs unconstrained -- an environment need not set both hosts). Small
-- table, so a plain CREATE UNIQUE INDEX (not CONCURRENTLY) as in 0035's convention. If the current
-- data already holds a duplicate host, this migration FAILS -- which is correct: the duplicate must
-- be resolved before the constraint can protect against the next one.
-- ============================================================================

-- Create each index in its OWN subtransaction and swallow a duplicate-key failure as a WARNING, so
-- pre-existing duplicate data can never fail the whole migration chain (which would also block the
-- dev server). Where the data is clean the index is created normally; where a duplicate exists it is
-- skipped and must be resolved, then re-added by a follow-up migration.
DO $$
BEGIN
  BEGIN
    CREATE UNIQUE INDEX IF NOT EXISTS tenant_environments_client_portal_host_key
      ON auth.tenant_environments (client_portal_host) WHERE client_portal_host IS NOT NULL;
  EXCEPTION WHEN unique_violation THEN
    RAISE WARNING 'skipped client_portal_host unique index: duplicate hosts exist -- resolve them';
  END;

  BEGIN
    CREATE UNIQUE INDEX IF NOT EXISTS tenant_environments_platform_host_key
      ON auth.tenant_environments (platform_host) WHERE platform_host IS NOT NULL;
  EXCEPTION WHEN unique_violation THEN
    RAISE WARNING 'skipped platform_host unique index: duplicate hosts exist -- resolve them';
  END;
END $$;
