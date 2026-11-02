-- ============================================================================
-- 0004_portal_scope.sql -- make the CLIENT PORTAL a hard trust boundary.
--
-- Bug this closes: a login authenticated into a tenant's Client Portal org could still see
-- the whole firm's data if the underlying identity happened to be staff, carry
-- 'contacts:read_all', or be a SYSTEM_USER_IDS break-glass principal. The per-portal access
-- model explicitly lets one person hold BOTH a client and a platform login (docs/
-- identity-and-access.md), so "am I acting as a client right now?" must be decided by the
-- PORTAL the request came through, not by the person's other roles.
--
-- Fix: resolve_claims classifies the active org -> a `portal` claim (client|platform|
-- operations) from auth.tenant_environments, and the scope helpers key off it:
--   * auth.tenant_lineage() is EMPTY in the client portal (no firm-wide staff reach);
--   * auth.can_read_all() is FALSE in the client portal (the "see all clients" lever and
--     the system bypass do not apply there);
--   * in the client portal a login's scope is ONLY the parties it is linked to via
--     crm.contact_users (never an adviser book).
-- Outside the client portal (portal NULL/'platform'/'operations') behaviour is unchanged,
-- so staff + the RLS tests are unaffected.
-- ============================================================================

-- The portal the current request came through (server-resolved in resolve_claims).
CREATE OR REPLACE FUNCTION auth.portal() RETURNS text LANGUAGE sql STABLE AS $$
  SELECT NULLIF(auth.jwt() ->> 'portal', '') $$;

-- Staff reach: the caller's tenant + descendants. EMPTY for a client end-user -- decided by
-- the PORTAL (a client-portal session), with the legacy role='client' check kept as a belt.
CREATE OR REPLACE FUNCTION auth.tenant_lineage() RETURNS int[] LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN auth.portal() = 'client' OR auth.role() = 'client' THEN '{}'::int[]
    WHEN auth.jwt() ? 'tenant_ids'
      THEN ARRAY(SELECT jsonb_array_elements_text(auth.jwt() -> 'tenant_ids')::int)
    ELSE COALESCE(
      (SELECT array_agg(id) FROM auth.tenants WHERE auth.tenant_id() = ANY(lineage)),
      '{}'::int[])
  END $$;

-- "See every client in the firm": a platform-side entitlement. NEVER granted in the client
-- portal (COALESCE so a missing portal claim -- e.g. the RLS unit tests -- reads as non-client
-- and behaves exactly as before). has_permission() still folds in the system bypass, which is
-- why it is gated here on the portal rather than left to leak into a client session.
CREATE OR REPLACE FUNCTION auth.can_read_all() RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT COALESCE(auth.portal(), '') <> 'client'
     AND auth.has_permission('contacts:read_all') $$;

-- resolve_claims (re-defined, cf. 0002): now also classifies the active org into a `portal`
-- claim and scopes a client-portal session to its OWN linked parties only.
CREATE OR REPLACE FUNCTION auth.resolve_claims(p_workos_user_id text, p_workos_org_id text)
  RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = 'auth' AS $fn$
DECLARE
  v_user_uuid   uuid;
  v_tenant_id   int;
  v_tenant_ids  int[];
  v_portal      text;
  v_contact_ids int[];
  v_account_ids int[];
BEGIN
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

  -- Classify the active org -> portal (project-per-tenant: org = portal). Unknown/unmatched
  -- defaults to 'platform' (a staff context); the client portal is matched explicitly so it
  -- can never be widened. Operations goes through the admin (service_role) path, not RLS.
  SELECT CASE
           WHEN p_workos_org_id = te.client_portal_org_id THEN 'client'
           WHEN p_workos_org_id = te.operations_org_id     THEN 'operations'
           ELSE 'platform'
         END
    INTO v_portal
    FROM auth.tenant_environments te
    WHERE te.tenant_id = v_tenant_id
    ORDER BY te.id
    LIMIT 1;
  v_portal := COALESCE(v_portal, 'platform');

  -- Client scope. In the client portal: ONLY the parties this login is linked to
  -- (crm.contact_users) -- never an adviser book. Elsewhere (staff): the login's own links
  -- PLUS its assigned book (crm.client_advisers).
  IF v_user_uuid IS NOT NULL THEN
    IF v_portal = 'client' THEN
      SELECT COALESCE(array_agg(DISTINCT contact_id), '{}'::int[]) INTO v_contact_ids
        FROM crm.contact_users WHERE user_id = v_user_uuid;
    ELSE
      SELECT COALESCE(array_agg(DISTINCT cid), '{}'::int[]) INTO v_contact_ids FROM (
        SELECT contact_id AS cid FROM crm.contact_users   WHERE user_id = v_user_uuid
        UNION
        SELECT contact_id AS cid FROM crm.client_advisers WHERE adviser_id = v_user_uuid
      ) s;
    END IF;
  ELSE
    v_contact_ids := '{}'::int[];
  END IF;

  IF array_length(v_contact_ids, 1) IS NOT NULL THEN
    SELECT COALESCE(array_agg(DISTINCT aid), '{}'::int[]) INTO v_account_ids FROM (
      SELECT id AS aid FROM platform.accounts WHERE contact_id = ANY(v_contact_ids)
      UNION
      SELECT account_id AS aid FROM crm.account_contacts
        WHERE contact_id = ANY(v_contact_ids) AND account_id IS NOT NULL
    ) s;
  ELSE
    v_account_ids := '{}'::int[];
  END IF;

  RETURN jsonb_build_object(
    'user_uuid',   v_user_uuid,
    'tenant_id',   v_tenant_id,
    'tenant_ids',  to_jsonb(v_tenant_ids),
    'portal',      v_portal,
    'contact_ids', to_jsonb(v_contact_ids),
    'account_ids', to_jsonb(v_account_ids)
  );
END;
$fn$;

GRANT EXECUTE ON FUNCTION auth.resolve_claims(text, text) TO authenticated, service_role;
ALTER FUNCTION auth.resolve_claims(text, text) OWNER TO service_role;
