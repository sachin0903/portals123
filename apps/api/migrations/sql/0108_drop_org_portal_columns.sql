-- ============================================================================
-- 0106_drop_org_portal_columns.sql — delete the org-per-portal schema.
--
-- 0105 made the portal app-only; nothing reads the per-portal org columns or the legacy
-- org-per-tenant column any more. This drops them, plus the two SQL helpers that existed only to
-- read them. Guarded by an assertion that every live login carries its tenant — the one thing the
-- deleted fallbacks could still have resolved.
-- ============================================================================

-- Every non-deleted login must know its tenant: resolve_claims (0105) reads ONLY auth.users.
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM auth.users WHERE tenant_id IS NULL AND deleted_at IS NULL;
  IF n > 0 THEN
    RAISE EXCEPTION
      '0106: % live auth.users row(s) have tenant_id NULL — backfill them before dropping the '
      'workos_organisation_id fallback', n;
  END IF;
END $$;

-- auth.tenant_id(): claims only. The workos_organisation_id psql fallback dies with the column.
CREATE OR REPLACE FUNCTION auth.tenant_id() RETURNS int LANGUAGE sql STABLE AS $$
  SELECT NULLIF(auth.jwt() ->> 'tenant_id', '')::int $$;

COMMENT ON FUNCTION auth.tenant_id() IS
  'Active tenant id, from the pre-resolved claims only (0106). A session without claims has no '
  'tenant — there is no org-based fallback.';

DROP FUNCTION IF EXISTS auth.tenant_by_workos_org(text);
DROP FUNCTION IF EXISTS auth.workos_org();

DROP INDEX IF EXISTS auth.tenants_workos_organisation_id_idx;
ALTER TABLE auth.tenants DROP COLUMN IF EXISTS workos_organisation_id;

ALTER TABLE auth.tenant_environments
  DROP COLUMN IF EXISTS client_portal_org_id,
  DROP COLUMN IF EXISTS platform_org_id,
  DROP COLUMN IF EXISTS operations_org_id;
