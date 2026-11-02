-- ============================================================================
-- 0094_portal_from_application.sql -- classify the `portal` claim from the WorkOS APPLICATION
-- (the token's client id), falling back to the org-based classification.
--
-- Step P3 of org-per-portal -> application-per-portal (docs/auth-applications-migration.md, A1).
--
-- auth.resolve_claims gains an optional third argument, p_client_id (the token's `client_id`/`aud`
-- claim -- the WorkOS APPLICATION the session signed in through). The portal is now classified from
-- it, matched against the per-application columns 0093 added; when p_client_id is NULL or matches no
-- configured application, classification FALLS BACK to the existing org-based branch, unchanged.
--
-- SAFETY -- this is behaviour-preserving until a firm's application ids are populated:
--   * 0093's *_client_id columns are all NULL today, so `p_client_id = te.*_client_id` is never true
--     (NULL comparisons are never true), and every call falls through to the org branch => byte-
--     identical to 0035 on the current data.
--   * A WRONG claim name (so p_client_id is NULL/garbage) cannot widen anything: it simply fails to
--     match and the org branch decides, exactly as today. The application path can only ACTIVATE once
--     the columns are set AND the token carries the matching client id -- i.e. per-firm, deliberately.
--   * The application is the preferred portal signal once configured (a per-app sign-in mints a
--     token whose client id IS the portal), so it is checked before the org branch -- BUT WorkOS
--     does not restrict which application an environment account can sign into, so the app only
--     PROPOSES the portal: a staff/ops portal is granted only when the user holds an active
--     membership for it, else it downgrades to 'client'. The app path can therefore never hand a
--     non-member a staff lineage. The org fallback stays ungated (org membership is its gate).
--
-- Everything else in the function -- the 0035 containment guard, the tenant/lineage resolution, the
-- tenant-scoped contact/account/role scope -- is preserved verbatim. p_client_id is NOT added to the
-- containment guard: it only reclassifies the CALLER'S OWN portal (it never selects whose scope is
-- returned -- that stays keyed on the resolved user/tenant), so it cannot leak another principal's
-- scope, and the return value cannot be used to escalate without set_config (forbidden on the one
-- authenticated SQL surface -- see 0035's header).
--
-- Signature change: adding a parameter makes a new function identity, so the 2-arg form is dropped
-- and recreated as a 3-arg with p_client_id DEFAULT NULL -- so existing 2-arg callers (the RLS unit
-- tests) still resolve via the default, and db.py passes all three.
-- ============================================================================

DROP FUNCTION IF EXISTS auth.resolve_claims(text, text);

CREATE FUNCTION auth.resolve_claims(
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
  -- is deliberately NOT part of this guard (it reclassifies the caller's own portal only; see header).
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

  -- Classify the portal. The APPLICATION (0093/0094) is the preferred signal: the token's client id
  -- says which surface the session signed in through. But WorkOS does NOT restrict which application
  -- an account in the environment may sign into, so a client could obtain a token for the PLATFORM
  -- app -- and taking the portal from the app alone would then hand them a staff lineage and the
  -- firm's whole book. So the app PROPOSES; our membership table DISPOSES: a staff/ops portal is only
  -- granted when the user holds an active membership for it, else it is downgraded to 'client'
  -- (own-scope only). (The sign-in path refuses such a session outright for UX; this is the hard
  -- data-safety gate behind it.) The client portal needs no such check -- it is the least privilege.
  --
  -- The ORG fallback (0004/0035) is used verbatim when p_client_id is NULL or matches no configured
  -- application, and is left UNGATED -- so behaviour is byte-identical until a firm's *_client_id
  -- columns are populated (org membership was, and remains, the gate on that path).
  SELECT
    CASE WHEN p_client_id = te.client_portal_client_id THEN 'client'
         WHEN p_client_id = te.operations_client_id    THEN 'operations'
         WHEN p_client_id = te.platform_client_id      THEN 'platform' END,
    CASE WHEN p_workos_org_id = te.client_portal_org_id THEN 'client'
         WHEN p_workos_org_id = te.operations_org_id     THEN 'operations'
         ELSE 'platform' END
    INTO v_app_portal, v_org_portal
    FROM auth.tenant_environments te
    WHERE te.tenant_id = v_tenant_id
    ORDER BY te.id
    LIMIT 1;

  IF v_app_portal IS NOT NULL THEN
    v_portal := v_app_portal;
    -- Membership gate: never grant a staff/ops portal from the app alone (see above).
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

  -- Client scope. In the client portal: ONLY the parties this login is linked to
  -- (crm.contact_users) -- never an adviser book. Elsewhere (staff): own links PLUS assigned book.
  -- Every lookup ANDed with the resolved tenant (0035).
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

GRANT EXECUTE ON FUNCTION auth.resolve_claims(text, text, text) TO authenticated, service_role;
ALTER FUNCTION auth.resolve_claims(text, text, text) OWNER TO service_role;

COMMENT ON FUNCTION auth.resolve_claims(text, text, text) IS
  'The claim minter (SECURITY DEFINER, service_role/BYPASSRLS). Portal is classified from the WorkOS '
  'APPLICATION (p_client_id, 0094) when configured, else the org (0004/0035). CONTAINED (0035): a '
  'session with published claims may only resolve its OWN identity. Behaviour-preserving until a '
  'firm''s *_client_id columns (0093) are populated.';
