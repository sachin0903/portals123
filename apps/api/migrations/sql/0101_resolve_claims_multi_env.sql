-- ============================================================================
-- 0097_resolve_claims_multi_env.sql -- classify the portal robustly when a tenant has MORE THAN ONE
-- environment row.
--
-- 0094 classified the portal by reading a SINGLE environment row (`ORDER BY te.id LIMIT 1`). A tenant
-- can legitimately have several `auth.tenant_environments` rows (one per stage, or a stale/extra row),
-- and `set_environment_applications` writes the app ids to the INSTANCE-STAGE row (primary_environment_id)
-- while that LIMIT 1 read took the LOWEST-id row -- so a saved app id could land on a row the classifier
-- never looked at, and app-based classification silently never activated (observed on the dev tenant,
-- which had two rows). The org fallback had the same single-row fragility.
--
-- Fix: match p_client_id / p_workos_org_id against ANY of the tenant's environment rows (EXISTS), so
-- classification no longer depends on which row happens to be lowest-id. Everything else in the
-- function -- the 0035 containment guard, tenant/lineage resolution, the membership gate that keeps a
-- non-member off a staff/ops portal (0094), and the tenant-scoped contact/account/role scope -- is
-- preserved verbatim. CREATE OR REPLACE (same 3-arg signature), so no callers change.
-- ============================================================================

CREATE OR REPLACE FUNCTION auth.resolve_claims(
  p_workos_user_id text,
  p_workos_org_id text,
  p_client_id text DEFAULT NULL
)
  RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = 'auth' AS $fn$
DECLARE
  v_user_uuid   uuid;
  v_tenant_id   int;
  v_tenant_ids  int[];
  v_portal      text;
  v_app_portal  text;   -- portal PROPOSED by the token's application (client id), NULL if unmatched
  v_org_portal  text;   -- portal from the org (the 0004/0035 fallback)
  v_contact_ids int[];
  v_account_ids int[];
  v_role_ids    int[];
  v_claims      jsonb := auth.jwt();
BEGIN
  -- CONTAINMENT (0035): a session with published claims may only resolve its own identity. p_client_id
  -- is deliberately NOT part of this guard (it reclassifies the caller's own portal only).
  IF v_claims <> '{}'::jsonb AND NOT auth.is_system() AND NOT (
       p_workos_user_id IS NOT NULL
       AND p_workos_user_id = v_claims ->> 'sub'
       AND (p_workos_org_id IS NULL OR p_workos_org_id = v_claims ->> 'org_id')
     ) THEN
    RAISE EXCEPTION
      'auth.resolve_claims: a session with published claims may only resolve its own identity'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  SELECT id, tenant_id INTO v_user_uuid, v_tenant_id FROM auth.users
    WHERE workos_user_id = p_workos_user_id AND deleted_at IS NULL;

  IF v_tenant_id IS NULL THEN
    SELECT id INTO v_tenant_id FROM auth.tenants
      WHERE workos_organisation_id = p_workos_org_id;
  END IF;

  IF v_tenant_id IS NOT NULL THEN
    SELECT COALESCE(array_agg(id), '{}'::int[]) INTO v_tenant_ids
      FROM auth.tenants WHERE v_tenant_id = ANY(lineage);
  ELSE
    v_tenant_ids := '{}'::int[];
  END IF;

  -- Classify the portal from the APPLICATION (0093/0094), matched against ANY of the tenant's
  -- environment rows (0097) -- a tenant may have several, and the app ids can live on any of them.
  -- WorkOS does not restrict which application an environment account can sign into, so the app only
  -- PROPOSES the portal; the membership gate below is what keeps a non-member off a staff/ops portal.
  IF p_client_id IS NOT NULL AND v_tenant_id IS NOT NULL THEN
    SELECT CASE
      WHEN EXISTS (SELECT 1 FROM auth.tenant_environments te
                   WHERE te.tenant_id = v_tenant_id AND te.client_portal_client_id = p_client_id)
        THEN 'client'
      WHEN EXISTS (SELECT 1 FROM auth.tenant_environments te
                   WHERE te.tenant_id = v_tenant_id AND te.operations_client_id = p_client_id)
        THEN 'operations'
      WHEN EXISTS (SELECT 1 FROM auth.tenant_environments te
                   WHERE te.tenant_id = v_tenant_id AND te.platform_client_id = p_client_id)
        THEN 'platform'
    END INTO v_app_portal;
  END IF;

  -- The org fallback (0004/0035), also matched across ALL the tenant's environment rows. Unknown/
  -- unmatched defaults to 'platform' (staff); 'client'/'operations' matched explicitly so neither can
  -- be silently widened. Left UNGATED (org membership is its gate) so it is behaviour-preserving.
  IF v_tenant_id IS NOT NULL THEN
    SELECT CASE
      WHEN EXISTS (SELECT 1 FROM auth.tenant_environments te
                   WHERE te.tenant_id = v_tenant_id AND te.client_portal_org_id = p_workos_org_id)
        THEN 'client'
      WHEN EXISTS (SELECT 1 FROM auth.tenant_environments te
                   WHERE te.tenant_id = v_tenant_id AND te.operations_org_id = p_workos_org_id)
        THEN 'operations'
      ELSE 'platform'
    END INTO v_org_portal;
  END IF;

  IF v_app_portal IS NOT NULL THEN
    v_portal := v_app_portal;
    -- Membership gate: never grant a staff/ops portal from the app alone (0094).
    IF v_portal IN ('platform', 'operations')
       AND v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL
       AND NOT EXISTS (
         SELECT 1 FROM auth.organisation_memberships
         WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id
           AND portal = v_portal AND status = 'active'
       ) THEN
      v_portal := 'client';
    END IF;
  ELSE
    v_portal := COALESCE(v_org_portal, 'platform');
  END IF;

  -- Client scope. Client portal: ONLY the parties this login is linked to (crm.contact_users) --
  -- never an adviser book. Staff: own links PLUS assigned book. Every lookup ANDed with the tenant (0035).
  IF v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL THEN
    IF v_portal = 'client' THEN
      SELECT COALESCE(array_agg(DISTINCT contact_id), '{}'::int[]) INTO v_contact_ids
        FROM crm.contact_users WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id;
    ELSE
      SELECT COALESCE(array_agg(DISTINCT cid), '{}'::int[]) INTO v_contact_ids FROM (
        SELECT contact_id AS cid FROM crm.contact_users
          WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id
        UNION
        SELECT contact_id AS cid FROM crm.client_advisers
          WHERE adviser_id = v_user_uuid AND tenant_id = v_tenant_id
      ) s;
    END IF;
  ELSE
    v_contact_ids := '{}'::int[];
  END IF;

  IF array_length(v_contact_ids, 1) IS NOT NULL THEN
    SELECT COALESCE(array_agg(DISTINCT aid), '{}'::int[]) INTO v_account_ids FROM (
      SELECT id AS aid FROM platform.accounts
        WHERE contact_id = ANY(v_contact_ids) AND tenant_id = v_tenant_id
      UNION
      SELECT account_id AS aid FROM crm.account_contacts
        WHERE contact_id = ANY(v_contact_ids) AND account_id IS NOT NULL
          AND tenant_id = v_tenant_id
    ) s;
  ELSE
    v_account_ids := '{}'::int[];
  END IF;

  -- Roles this login actively holds for the resolved portal (drives role-assigned task filtering).
  IF v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL THEN
    SELECT COALESCE(array_agg(DISTINCT role_id), '{}'::int[]) INTO v_role_ids
      FROM auth.organisation_memberships
      WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id AND portal = v_portal
        AND status = 'active' AND role_id IS NOT NULL;
  ELSE
    v_role_ids := '{}'::int[];
  END IF;

  RETURN jsonb_build_object(
    'user_uuid',   v_user_uuid,
    'tenant_id',   v_tenant_id,
    'tenant_ids',  to_jsonb(v_tenant_ids),
    'portal',      v_portal,
    'contact_ids', to_jsonb(v_contact_ids),
    'account_ids', to_jsonb(v_account_ids),
    'role_ids',    to_jsonb(v_role_ids)
  );
END;
$fn$;

ALTER FUNCTION auth.resolve_claims(text, text, text) OWNER TO service_role;
