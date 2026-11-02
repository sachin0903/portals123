-- ============================================================================
-- 0105_app_only_portal.sql — the portal is the APPLICATION, full stop.
--
-- The org-based portal classification (0004 → 0035 → 0101) is DELETED, not narrowed. The model is
-- now exactly one sentence long: a session's portal is the WorkOS application it signed in through,
-- and a person may use that portal only if they hold an ACTIVE auth.organisation_memberships row for
-- it. WorkOS authenticates (one firm org per tenant); OUR membership table is the whole of portal
-- access. No fallback, no default, no belt:
--
--   * p_client_id matched against the tenant's {client_portal,platform,operations}_client_id
--     columns (any environment row). No match ⇒ portal NULL ⇒ the request layer refuses (db.py).
--     The old fallback's `ELSE 'platform'` — an ungated staff default — is gone.
--   * EVERY portal is membership-gated now, client included. 0101 gated only staff/ops and let
--     `client` through as "least privilege"; but least privilege is still access, and "That account
--     does not have access to this portal" should mean exactly what it says.
--   * auth.tenant_lineage() and auth.can_read_all() become WHITELISTS on the portal claim
--     (IN ('platform','operations')) instead of blacklists on 'client' — a NULL/absent portal now
--     fails closed on both, which also retires the auth.role()='client' belt.
--   * auth.has_role() is dropped: no policy ever consumed it, and the role claim is display-only.
--
-- The tenant is resolved ONLY from auth.users.tenant_id — the workos_organisation_id fallback is
-- gone from the minter (the column itself is dropped by 0106).
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
  v_contact_ids int[];
  v_account_ids int[];
  v_role_ids    int[];
  v_claims      jsonb := auth.jwt();
BEGIN
  -- CONTAINMENT (0035): a session with published claims may only resolve its own identity.
  IF v_claims <> '{}'::jsonb AND NOT auth.is_system() AND NOT (
       p_workos_user_id IS NOT NULL
       AND p_workos_user_id = v_claims ->> 'sub'
       AND (p_workos_org_id IS NULL OR p_workos_org_id = v_claims ->> 'org_id')
     ) THEN
    RAISE EXCEPTION
      'auth.resolve_claims: a session with published claims may only resolve its own identity'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  -- Identity: the login IS the tenant (project-per-tenant). No org fallback.
  SELECT id, tenant_id INTO v_user_uuid, v_tenant_id FROM auth.users
    WHERE workos_user_id = p_workos_user_id AND deleted_at IS NULL;

  IF v_tenant_id IS NOT NULL THEN
    SELECT COALESCE(array_agg(id), '{}'::int[]) INTO v_tenant_ids
      FROM auth.tenants WHERE v_tenant_id = ANY(lineage);
  ELSE
    v_tenant_ids := '{}'::int[];
  END IF;

  -- Portal: the application the token was minted for, held only with an ACTIVE membership for it.
  -- Anything else is NULL and the request layer refuses.
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

  -- Scope. Client portal: ONLY the parties this login is linked to — never an adviser book.
  -- Staff: own links PLUS assigned book. No portal: nothing.
  IF v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL AND v_portal = 'client' THEN
    SELECT COALESCE(array_agg(DISTINCT contact_id), '{}'::int[]) INTO v_contact_ids
      FROM crm.contact_users WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id;
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

  -- Roles this login actively holds for the resolved portal (drives role_ids -> permissions).
  IF v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL AND v_portal IS NOT NULL THEN
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

COMMENT ON FUNCTION auth.resolve_claims(text, text, text) IS
  'The claim minter (SECURITY DEFINER, service_role/BYPASSRLS). App-only (0105): the portal is the '
  'WorkOS application the token was minted for, held only with an ACTIVE organisation_memberships '
  'row for it — no org fallback, no default. NULL portal ⇒ the request layer refuses. CONTAINED '
  '(0035): a session with published claims may only resolve its OWN identity.';

-- Staff reach is a WHITELIST on the portal claim now. NULL/absent portal ⇒ empty lineage — the
-- claims-less psql fallback and the role='client' belt are gone with the org model.
CREATE OR REPLACE FUNCTION auth.tenant_lineage() RETURNS int[] LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN auth.portal() IN ('platform', 'operations') THEN
      CASE WHEN auth.jwt() ? 'tenant_ids'
        THEN ARRAY(SELECT jsonb_array_elements_text(auth.jwt() -> 'tenant_ids')::int)
        ELSE auth.tenant_lineage_of(auth.tenant_id()) END
    ELSE '{}'::int[]
  END $$;

COMMENT ON FUNCTION auth.tenant_lineage() IS
  'Staff reach: the caller''s tenant + descendants, ONLY for a platform/operations portal session '
  '(whitelist, 0105). A client session — or any session without a portal claim — gets ''{}''.';

-- Same whitelist for the firm-wide read entitlement.
CREATE OR REPLACE FUNCTION auth.can_read_all() RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT auth.portal() IN ('platform', 'operations')
     AND auth.has_permission('contacts:read_all') $$;

-- No policy ever consumed auth.has_role(); the role claim is display-only. Gone.
DROP FUNCTION IF EXISTS auth.has_role(text);
