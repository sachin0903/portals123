-- 0119_access_profiles_and_multi_roles
--
-- A login is deliberately one of:
--   staff        a firm worker, allowed only platform/operations memberships
--   profile      a client-facing identity, allowed only a client membership
--   unclassified imported from WorkOS without our app deciding what it may use
--
-- The old model explicitly allowed one person to hold both Client and Platform access. That made
-- the Team directory a pair of portal switches rather than a staff directory, and let a mistaken
-- click turn a client login into a firm user. The boundary is now data, enforced below the API.
--
-- A portal membership also gains a SET of roles. organisation_memberships.role_id remains as the
-- primary/cosmetic WorkOS role during the transition; authorisation reads membership_roles.

ALTER TABLE auth.users
  ADD COLUMN access_kind text NOT NULL DEFAULT 'unclassified'
  CHECK (access_kind IN ('staff', 'profile', 'unclassified'));

-- Refuse rather than guess if a historical identity crossed the new boundary. Such an identity
-- cannot be split safely in SQL: WorkOS owns the unique email/user and a second credential needs an
-- intentional address and invitation. Environments with none (including dev at authoring time)
-- migrate automatically.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM auth.organisation_memberships
    GROUP BY user_id
    HAVING bool_or(portal IN ('platform', 'operations')) AND bool_or(portal = 'client')
  ) THEN
    RAISE EXCEPTION
      '0119: dual client/platform identities must be split before migration';
  END IF;
END $$;

UPDATE auth.users u
SET access_kind = CASE
  WHEN EXISTS (
    SELECT 1 FROM auth.organisation_memberships m
    WHERE m.user_id = u.id AND m.portal = 'client'
  ) THEN 'profile'
  WHEN EXISTS (
    SELECT 1 FROM auth.organisation_memberships m
    WHERE m.user_id = u.id AND m.portal IN ('platform', 'operations')
  ) THEN 'staff'
  ELSE 'unclassified'
END;

CREATE OR REPLACE FUNCTION auth.enforce_membership_access_kind()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = 'auth'
AS $$
DECLARE
  v_kind text;
  v_user_tenant int;
BEGIN
  SELECT access_kind, tenant_id INTO v_kind, v_user_tenant
  FROM auth.users
  WHERE id = NEW.user_id AND deleted_at IS NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'membership user is not an active login'
      USING ERRCODE = 'foreign_key_violation';
  END IF;
  IF v_user_tenant IS NULL THEN
    UPDATE auth.users SET tenant_id = NEW.tenant_id WHERE id = NEW.user_id;
  ELSIF v_user_tenant IS DISTINCT FROM NEW.tenant_id THEN
    RAISE EXCEPTION 'membership user belongs to another tenant'
      USING ERRCODE = 'foreign_key_violation';
  END IF;

  -- A WorkOS reconcile deliberately imports people as unclassified. Their first membership is the
  -- explicit classification event, including service-role fixtures and operational repair SQL.
  IF v_kind = 'unclassified' THEN
    v_kind := CASE WHEN NEW.portal = 'client' THEN 'profile' ELSE 'staff' END;
    UPDATE auth.users SET access_kind = v_kind WHERE id = NEW.user_id;
  END IF;

  IF NEW.portal = 'client' AND v_kind <> 'profile' THEN
    RAISE EXCEPTION 'only profile identities may hold client portal access'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.portal IN ('platform', 'operations') AND v_kind <> 'staff' THEN
    RAISE EXCEPTION 'only staff identities may hold staff portal access'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

ALTER FUNCTION auth.enforce_membership_access_kind() OWNER TO service_role;

CREATE TRIGGER organisation_memberships_access_kind
BEFORE INSERT OR UPDATE OF user_id, tenant_id, portal
ON auth.organisation_memberships
FOR EACH ROW EXECUTE FUNCTION auth.enforce_membership_access_kind();

CREATE OR REPLACE FUNCTION auth.enforce_user_access_kind()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = 'auth'
AS $$
BEGIN
  IF NEW.access_kind = 'profile' AND EXISTS (
    SELECT 1 FROM auth.organisation_memberships m
    WHERE m.user_id = NEW.id AND m.portal IN ('platform', 'operations')
  ) THEN
    RAISE EXCEPTION 'a staff identity with staff memberships cannot become a profile'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.access_kind = 'staff' AND EXISTS (
    SELECT 1 FROM auth.organisation_memberships m
    WHERE m.user_id = NEW.id AND m.portal = 'client'
  ) THEN
    RAISE EXCEPTION 'a profile with client memberships cannot become staff'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.access_kind = 'unclassified' AND EXISTS (
    SELECT 1 FROM auth.organisation_memberships m WHERE m.user_id = NEW.id
  ) THEN
    RAISE EXCEPTION 'an identity with portal access cannot become unclassified'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

ALTER FUNCTION auth.enforce_user_access_kind() OWNER TO service_role;

CREATE TRIGGER users_access_kind
BEFORE UPDATE OF access_kind
ON auth.users
FOR EACH ROW EXECUTE FUNCTION auth.enforce_user_access_kind();

CREATE TABLE auth.membership_roles (
  membership_id int NOT NULL REFERENCES auth.organisation_memberships(id) ON DELETE CASCADE,
  role_id int NOT NULL REFERENCES auth.roles(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (membership_id, role_id)
);
CREATE INDEX membership_roles_role_id_idx ON auth.membership_roles(role_id);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM auth.organisation_memberships m
    JOIN auth.roles r ON r.id = m.role_id
    WHERE r.tenant_id IS DISTINCT FROM m.tenant_id
       OR COALESCE(r.portal, 'platform') IS DISTINCT FROM m.portal
  ) THEN
    RAISE EXCEPTION '0119: existing membership role crosses its tenant or portal';
  END IF;
END $$;

INSERT INTO auth.membership_roles (membership_id, role_id)
SELECT id, role_id
FROM auth.organisation_memberships
WHERE role_id IS NOT NULL
ON CONFLICT DO NOTHING;

-- A role can only be attached to a membership in the same tenant and portal. This is the
-- privilege-escalation boundary: without it a client profile could be handed a platform role whose
-- permissions auth.member_permissions would then union into the session.
CREATE OR REPLACE FUNCTION auth.enforce_membership_role_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = 'auth'
AS $$
DECLARE
  v_membership auth.organisation_memberships%ROWTYPE;
  v_role auth.roles%ROWTYPE;
BEGIN
  SELECT * INTO v_membership
  FROM auth.organisation_memberships
  WHERE id = NEW.membership_id;
  SELECT * INTO v_role
  FROM auth.roles
  WHERE id = NEW.role_id;

  IF v_membership.id IS NULL OR v_role.id IS NULL
     OR v_role.tenant_id IS DISTINCT FROM v_membership.tenant_id
     OR COALESCE(v_role.portal, 'platform') IS DISTINCT FROM v_membership.portal THEN
    RAISE EXCEPTION 'membership role must belong to the membership tenant and portal'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

ALTER FUNCTION auth.enforce_membership_role_scope() OWNER TO service_role;

CREATE TRIGGER membership_roles_scope
BEFORE INSERT OR UPDATE OF membership_id, role_id
ON auth.membership_roles
FOR EACH ROW EXECUTE FUNCTION auth.enforce_membership_role_scope();

-- Keep the transitional primary role and the role set coherent for legacy writers and fixtures
-- that insert/update organisation_memberships directly. New application writes replace the full
-- set through provisioning.set_member_roles.
CREATE OR REPLACE FUNCTION auth.sync_primary_membership_role()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = 'auth'
AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.role_id IS DISTINCT FROM NEW.role_id AND OLD.role_id IS NOT NULL THEN
    DELETE FROM auth.membership_roles
    WHERE membership_id = NEW.id AND role_id = OLD.role_id;
  END IF;
  IF NEW.role_id IS NOT NULL THEN
    INSERT INTO auth.membership_roles (membership_id, role_id)
    VALUES (NEW.id, NEW.role_id)
    ON CONFLICT DO NOTHING;
  END IF;
  RETURN NEW;
END;
$$;

ALTER FUNCTION auth.sync_primary_membership_role() OWNER TO service_role;

CREATE TRIGGER organisation_memberships_primary_role
AFTER INSERT OR UPDATE OF role_id
ON auth.organisation_memberships
FOR EACH ROW EXECUTE FUNCTION auth.sync_primary_membership_role();

CREATE OR REPLACE FUNCTION auth.enforce_role_membership_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = 'auth'
AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM auth.membership_roles mr
    JOIN auth.organisation_memberships m ON m.id = mr.membership_id
    WHERE mr.role_id = NEW.id
      AND (
        m.tenant_id IS DISTINCT FROM NEW.tenant_id
        OR m.portal IS DISTINCT FROM COALESCE(NEW.portal, 'platform')
      )
  ) THEN
    RAISE EXCEPTION 'an assigned role cannot move to another tenant or portal'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

ALTER FUNCTION auth.enforce_role_membership_scope() OWNER TO service_role;

CREATE TRIGGER roles_membership_scope
BEFORE UPDATE OF tenant_id, portal
ON auth.roles
FOR EACH ROW EXECUTE FUNCTION auth.enforce_role_membership_scope();

ALTER TABLE auth.membership_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.membership_roles FORCE ROW LEVEL SECURITY;
CREATE POLICY membership_roles_read ON auth.membership_roles
  FOR SELECT TO authenticated
  USING (
    EXISTS (
      SELECT 1
      FROM auth.organisation_memberships m
      WHERE m.id = membership_roles.membership_id
        AND m.tenant_id = ANY(auth.tenant_lineage())
    )
    OR EXISTS (
      SELECT 1
      FROM auth.organisation_memberships m
      WHERE m.id = membership_roles.membership_id
        AND m.user_id = auth.uid()
    )
  );
GRANT SELECT ON auth.membership_roles TO authenticated;
GRANT ALL ON auth.membership_roles TO service_role;

SELECT audit.attach(ARRAY['auth.membership_roles']);

-- App-only portal claim minter, copied from 0107 with one deliberate change: role_ids is the set
-- attached through membership_roles rather than the membership's one primary/cosmetic role_id.
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
  'App-only claim minter. 0119: role_ids is the union of auth.membership_roles attached to the '
  'active membership for the application-resolved portal.';
