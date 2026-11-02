-- 0120_access_repair
--
-- 0119 landed its DDL and silently skipped every one of its DATA steps, which took each session's
-- permissions away: `auth.resolve_claims` now reads role_ids from auth.membership_roles, and the
-- backfill that was meant to fill that table inserted nothing.
--
-- The cause is the trap migrations/bootstrap/01-roles.sql states plainly and 0119 forgot: a
-- migration runs as `migrator`, auth.* carries FORCE ROW LEVEL SECURITY, and FORCE binds the table
-- owner too. The policies are `TO authenticated`, so as `migrator` every auth.* table reads as
-- EMPTY. `INSERT INTO auth.membership_roles SELECT ... FROM auth.organisation_memberships`
-- therefore inserted 0 rows, `UPDATE auth.users SET access_kind = ...` matched 0 rows (44 of 44
-- identities were left 'unclassified'), and — worst — both of 0119's `RAISE EXCEPTION` guards were
-- dead code: the dual-identity guard could not see the 16 dual identities on the dev book and the
-- role-scope guard could not see the 13 memberships whose role crosses its portal. A guard that
-- reads no rows does not refuse; it passes. So DML in an auth migration runs as service_role, and
-- a guard is only a guard once it can see.
--
-- Two policy changes come with the repair, because replaying 0119's intent verbatim would take
-- every staff session's permissions away a second time.

-- 1. The role/membership scope rule is about the CLIENT/STAFF boundary, not the exact portal.
--
-- 0119 required role.portal = membership.portal. The escalation it exists to prevent is the one
-- named in its own comment: a client profile handed a staff role, whose permissions
-- auth.member_permissions would union into a client session. Requiring an exact match also
-- refuses platform-versus-operations drift among firm STAFF, which is pre-existing real data
-- (10 platform memberships on the dev book carry the operations 'admin' role, and both roles hold
-- `system`, so the pairing granted exactly what it grants now). Refusing it at migration time
-- cannot repair it — it can only decline to backfill, which reads to the operator as "every
-- member of staff lost their permissions on deploy". So the DB enforces the boundary that carries
-- the privilege, and app/provisioning.py::validate_member_role_set keeps requiring an exact portal
-- match for every NEW assignment, so the drift cannot grow.
CREATE OR REPLACE FUNCTION auth.portal_side(p_portal text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN COALESCE(p_portal, 'platform') = 'client' THEN 'client' ELSE 'staff' END;
$$;

ALTER FUNCTION auth.portal_side(text) OWNER TO service_role;

COMMENT ON FUNCTION auth.portal_side(text) IS
  'Which side of the client/staff boundary a portal sits on. 0120: the membership_roles scope rule.';

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
     OR auth.portal_side(v_role.portal) IS DISTINCT FROM auth.portal_side(v_membership.portal) THEN
    RAISE EXCEPTION 'membership role must belong to the membership tenant and client/staff side'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

ALTER FUNCTION auth.enforce_membership_role_scope() OWNER TO service_role;

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
        OR auth.portal_side(m.portal) IS DISTINCT FROM auth.portal_side(NEW.portal)
      )
  ) THEN
    RAISE EXCEPTION 'an assigned role cannot move to another tenant or client/staff side'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

ALTER FUNCTION auth.enforce_role_membership_scope() OWNER TO service_role;

-- 2. An unclassified identity that already holds memberships on BOTH sides stays unclassified.
--
-- 0119 classified an unclassified login by its first membership grant, which is right for one
-- imported from WorkOS. Applied to an identity that already holds both a client and a staff
-- membership it is not a classification but a contradiction: the UPDATE fires
-- auth.enforce_user_access_kind, which correctly refuses, and the refusal takes down the
-- unrelated membership write that triggered it. There are 16 such identities on the dev book, all
-- staff who also hold a client login for testing. Splitting one needs a second email address and
-- an invitation, which no migration can invent, so they are grandfathered: left unclassified,
-- still able to use what they hold, and visible to an operator on both Team and Profiles until a
-- human splits them. Every identity on one side only is classified, and every new one is
-- classified by its first grant, so the boundary holds everywhere it can be stated.
CREATE OR REPLACE FUNCTION auth.enforce_membership_access_kind()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = 'auth'
AS $$
DECLARE
  v_kind text;
  v_user_tenant int;
  v_other_side boolean;
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

  IF v_kind = 'unclassified' THEN
    SELECT EXISTS (
      SELECT 1 FROM auth.organisation_memberships m
      WHERE m.user_id = NEW.user_id
        AND m.id IS DISTINCT FROM NEW.id
        AND auth.portal_side(m.portal) IS DISTINCT FROM auth.portal_side(NEW.portal)
    ) INTO v_other_side;

    IF v_other_side THEN
      RETURN NEW;  -- grandfathered: a dual identity is split by a human, not by a trigger
    END IF;

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

-- 3. The repair itself, idempotent: an environment that has not run 0119 yet reaches this with the
-- same empty table, and one that has already been repaired matches nothing.
--
-- The DDL above runs as `migrator`, which owns schema auth. Only the DML drops to service_role,
-- and only because FORCE RLS binds the owner: this is the line 0119 was missing.
SET LOCAL ROLE service_role;

-- A membership whose primary role sits on the other side of the client/staff boundary keeps no
-- role. This is the one place the repair takes access away and it is deliberate: on the dev book
-- it is a CLIENT membership carrying the 68-permission `platform-admin` role, i.e. a client-portal
-- session holding every firm permission, and a platform membership carrying a client role that
-- grants none. Whoever needs a role here gets one named for the portal they are using.
UPDATE auth.organisation_memberships m
SET role_id = NULL
FROM auth.roles r
WHERE r.id = m.role_id
  AND auth.portal_side(r.portal) IS DISTINCT FROM auth.portal_side(m.portal);

INSERT INTO auth.membership_roles (membership_id, role_id)
SELECT id, role_id
FROM auth.organisation_memberships
WHERE role_id IS NOT NULL
ON CONFLICT DO NOTHING;

UPDATE auth.users u
SET access_kind = CASE
  WHEN EXISTS (
    SELECT 1 FROM auth.organisation_memberships m
    WHERE m.user_id = u.id AND m.portal = 'client'
  ) THEN 'profile'
  ELSE 'staff'
END
WHERE u.access_kind = 'unclassified'
  AND EXISTS (SELECT 1 FROM auth.organisation_memberships m WHERE m.user_id = u.id)
  -- One side only. A dual identity is grandfathered rather than contradicted (see 2 above).
  AND NOT (
    EXISTS (
      SELECT 1 FROM auth.organisation_memberships m
      WHERE m.user_id = u.id AND m.portal = 'client'
    )
    AND EXISTS (
      SELECT 1 FROM auth.organisation_memberships m
      WHERE m.user_id = u.id AND m.portal IN ('platform', 'operations')
    )
  );

-- Say what was left for a human. A count in the deploy log is the only thing that distinguishes
-- "this book has no dual identities" from "nobody has looked".
DO $$
DECLARE
  v_dual int;
  v_roleless int;
BEGIN
  SELECT count(*) INTO v_dual FROM (
    SELECT m.user_id
    FROM auth.organisation_memberships m
    GROUP BY m.user_id
    HAVING bool_or(m.portal = 'client')
       AND bool_or(m.portal IN ('platform', 'operations'))
  ) d;
  SELECT count(*) INTO v_roleless
  FROM auth.organisation_memberships
  WHERE role_id IS NULL;

  IF v_dual > 0 THEN
    RAISE WARNING '0120: % identities hold both client and staff access and stay unclassified '
      'until they are split by hand', v_dual;
  END IF;
  IF v_roleless > 0 THEN
    RAISE WARNING '0120: % memberships carry no role and therefore no permissions', v_roleless;
  END IF;
END $$;

RESET ROLE;

COMMENT ON COLUMN auth.users.access_kind IS
  'staff | profile | unclassified. 0120: an identity holding both sides stays unclassified until a '
  'human splits it; every single-sided and every new identity is classified.';
