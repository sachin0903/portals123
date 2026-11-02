-- 0156_jisa_registered_contact_scope
--
-- A JISA's owner is the child. The parent/guardian is ``registered_contact`` and is who we invite
-- to the portal. ``auth.resolve_claims`` previously put only ``crm.contact_users`` rows into a
-- client login's ``contact_ids``, so the adult could not see the child's contact (and the portal's
-- "For {child}" grouping never appeared). Include the account owner whenever this login is a
-- registered contact on that account. Account ids already followed ``account_contacts``, so the
-- JISA itself was visible; the missing piece was the child's contact id.
--
-- Copied from 0119's claim minter with that one UNION added.

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
  v_contact_ids int[];
  v_account_ids int[];
  v_role_ids    int[];
  v_claims      jsonb := auth.jwt();
BEGIN
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

  IF v_tenant_id IS NOT NULL THEN
    SELECT COALESCE(array_agg(id), '{}'::int[]) INTO v_tenant_ids
      FROM auth.tenants WHERE v_tenant_id = ANY(lineage);
  ELSE
    v_tenant_ids := '{}'::int[];
  END IF;

  IF p_client_id IS NOT NULL AND v_tenant_id IS NOT NULL AND v_user_uuid IS NOT NULL THEN
    SELECT candidate INTO v_portal FROM (
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
      END AS candidate
    ) c
    WHERE c.candidate IS NOT NULL
      AND EXISTS (
        SELECT 1 FROM auth.organisation_memberships m
        WHERE m.user_id = v_user_uuid AND m.tenant_id = v_tenant_id
          AND m.portal = c.candidate AND m.status = 'active'
      );
  END IF;

  IF v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL AND v_portal = 'client' THEN
    SELECT COALESCE(array_agg(DISTINCT cid), '{}'::int[]) INTO v_contact_ids FROM (
      SELECT contact_id AS cid FROM crm.contact_users
        WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id
      UNION
      SELECT a.contact_id AS cid
        FROM crm.contact_users cu
        JOIN crm.account_contacts ac
          ON ac.contact_id = cu.contact_id AND ac.tenant_id = cu.tenant_id
         AND ac.role = 'registered_contact'
        JOIN platform.accounts a
          ON a.id = ac.account_id AND a.tenant_id = ac.tenant_id
        WHERE cu.user_id = v_user_uuid AND cu.tenant_id = v_tenant_id
          AND a.contact_id IS NOT NULL
    ) s;
  ELSIF v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL
        AND v_portal IN ('platform', 'operations') THEN
    SELECT COALESCE(array_agg(DISTINCT cid), '{}'::int[]) INTO v_contact_ids FROM (
      SELECT contact_id AS cid FROM crm.contact_users
        WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id
      UNION
      SELECT contact_id AS cid FROM crm.client_advisers
        WHERE adviser_id = v_user_uuid AND tenant_id = v_tenant_id
    ) s;
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

  IF v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL AND v_portal IS NOT NULL THEN
    SELECT COALESCE(array_agg(DISTINCT mr.role_id), '{}'::int[]) INTO v_role_ids
    FROM auth.organisation_memberships m
    JOIN auth.membership_roles mr ON mr.membership_id = m.id
    WHERE m.user_id = v_user_uuid AND m.tenant_id = v_tenant_id
      AND m.portal = v_portal AND m.status = 'active';
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

COMMENT ON FUNCTION auth.resolve_claims(text, text, text) IS
  'App-only claim minter. 0156: a client login that is a JISA registered contact also sees the '
  'child owner. 0119: role_ids is the union of auth.membership_roles for the resolved portal.';
