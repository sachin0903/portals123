-- 0121_user_types
--
-- One shared identity classification for the access work and the client/staff work being built
-- alongside it: `auth.users.type` is an ARRAY of the portal sides an identity belongs to.
--
-- New identities still have a HARD boundary. They are `['client']` or `['staff']`; the database
-- refuses adding the opposite side. `['client', 'staff']` exists only to state the truth about the
-- dual identities grandfathered by 0120. An empty array is the old `unclassified` state.
--
-- `access_kind` remains temporarily as a synchronised compatibility column because migrations run
-- before the new API container is healthy. Dropping or renaming it here would make an old container
-- fail during a rolling deploy. New code reads `type`; a later contract migration can remove the
-- scalar once no deployed code does.

ALTER TABLE auth.users
  ADD COLUMN type text[] NOT NULL DEFAULT '{}'::text[],
  ADD CONSTRAINT users_type_values_check CHECK (
    type IN (
      '{}'::text[],
      ARRAY['client']::text[],
      ARRAY['staff']::text[],
      ARRAY['client', 'staff']::text[]
    )
  );

-- FORCE RLS binds the migrator too (the defect 0120 repaired), so every data statement in auth
-- runs as the bypass role.
SET LOCAL ROLE service_role;

UPDATE auth.users u
SET type = CASE
  WHEN EXISTS (
    SELECT 1 FROM auth.organisation_memberships m
    WHERE m.user_id = u.id AND m.portal = 'client'
  ) AND EXISTS (
    SELECT 1 FROM auth.organisation_memberships m
    WHERE m.user_id = u.id AND m.portal IN ('platform', 'operations')
  ) THEN ARRAY['client', 'staff']::text[]
  WHEN u.access_kind = 'profile' OR EXISTS (
    SELECT 1 FROM auth.organisation_memberships m
    WHERE m.user_id = u.id AND m.portal = 'client'
  ) THEN ARRAY['client']::text[]
  WHEN u.access_kind = 'staff' OR EXISTS (
    SELECT 1 FROM auth.organisation_memberships m
    WHERE m.user_id = u.id AND m.portal IN ('platform', 'operations')
  ) THEN ARRAY['staff']::text[]
  ELSE '{}'::text[]
END;

RESET ROLE;

-- Keep the scalar deploy-compatibility column and the array coherent whichever generation of the
-- API writes the row. A direct array write is also checked against existing memberships, because
-- otherwise changing `type` would be a way around organisation_memberships_access_kind.
CREATE OR REPLACE FUNCTION auth.sync_user_type_contract()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = 'auth'
AS $$
DECLARE
  v_type_changed boolean;
  v_kind_changed boolean;
BEGIN
  v_type_changed := TG_OP = 'UPDATE' AND NEW.type IS DISTINCT FROM OLD.type;
  v_kind_changed := TG_OP = 'UPDATE' AND NEW.access_kind IS DISTINCT FROM OLD.access_kind;

  IF TG_OP = 'INSERT' THEN
    IF NEW.type <> '{}'::text[] THEN
      NEW.access_kind := CASE
        WHEN NEW.type = ARRAY['client']::text[] THEN 'profile'
        WHEN NEW.type = ARRAY['staff']::text[] THEN 'staff'
        ELSE 'unclassified'
      END;
    ELSIF NEW.access_kind <> 'unclassified' THEN
      NEW.type := CASE NEW.access_kind
        WHEN 'profile' THEN ARRAY['client']::text[]
        WHEN 'staff' THEN ARRAY['staff']::text[]
        ELSE '{}'::text[]
      END;
    END IF;
    RETURN NEW;
  END IF;

  IF v_type_changed THEN
    -- 0120's dual rows may remain dual, but no singleton/untyped identity may become dual.
    IF NEW.type = ARRAY['client', 'staff']::text[]
       AND OLD.type <> ARRAY['client', 'staff']::text[] THEN
      RAISE EXCEPTION 'a client/staff identity must be split, not made dual'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.type @> ARRAY['client']::text[] AND EXISTS (
      SELECT 1 FROM auth.organisation_memberships m
      WHERE m.user_id = NEW.id AND m.portal = 'client'
    ) THEN
      RAISE EXCEPTION 'an identity with client access must retain client type'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NOT NEW.type @> ARRAY['staff']::text[] AND EXISTS (
      SELECT 1 FROM auth.organisation_memberships m
      WHERE m.user_id = NEW.id AND m.portal IN ('platform', 'operations')
    ) THEN
      RAISE EXCEPTION 'an identity with staff access must retain staff type'
        USING ERRCODE = 'check_violation';
    END IF;
    NEW.access_kind := CASE
      WHEN NEW.type = ARRAY['client']::text[] THEN 'profile'
      WHEN NEW.type = ARRAY['staff']::text[] THEN 'staff'
      ELSE 'unclassified'
    END;
  ELSIF v_kind_changed THEN
    -- Compatibility for a rolling deploy's old writer. It can claim an empty identity, but cannot
    -- use the scalar to alter a grandfathered dual row.
    IF OLD.type = ARRAY['client', 'staff']::text[] THEN
      RAISE EXCEPTION 'a grandfathered dual identity must be split by invitation'
        USING ERRCODE = 'check_violation';
    END IF;
    NEW.type := CASE NEW.access_kind
      WHEN 'profile' THEN ARRAY['client']::text[]
      WHEN 'staff' THEN ARRAY['staff']::text[]
      ELSE '{}'::text[]
    END;
  END IF;
  RETURN NEW;
END;
$$;

ALTER FUNCTION auth.sync_user_type_contract() OWNER TO service_role;

CREATE TRIGGER users_sync_type_contract
BEFORE INSERT OR UPDATE OF type, access_kind
ON auth.users
FOR EACH ROW EXECUTE FUNCTION auth.sync_user_type_contract();

-- The membership is the privilege-bearing operation. Read the shared array, claim an untyped user
-- on first grant, and refuse the opposite side. A grandfathered dual row already contains both and
-- therefore continues to function until a human splits it.
CREATE OR REPLACE FUNCTION auth.enforce_membership_access_kind()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = 'auth'
AS $$
DECLARE
  v_types text[];
  v_user_tenant int;
  v_required text;
BEGIN
  SELECT type, tenant_id INTO v_types, v_user_tenant
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

  v_required := auth.portal_side(NEW.portal);
  IF v_types = '{}'::text[] THEN
    UPDATE auth.users
    SET type = ARRAY[v_required]::text[],
        access_kind = CASE WHEN v_required = 'client' THEN 'profile' ELSE 'staff' END
    WHERE id = NEW.user_id;
    v_types := ARRAY[v_required]::text[];
  END IF;

  IF NOT v_types @> ARRAY[v_required]::text[] THEN
    IF v_required = 'client' THEN
      RAISE EXCEPTION 'only client identities may hold client portal access'
        USING ERRCODE = 'check_violation';
    ELSE
      RAISE EXCEPTION 'only staff identities may hold staff portal access'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

ALTER FUNCTION auth.enforce_membership_access_kind() OWNER TO service_role;

COMMENT ON COLUMN auth.users.type IS
  'Identity sides: [] unclassified, [client] profile, [staff] firm worker. [client,staff] is '
  'reserved for the dual identities grandfathered by 0120; new cross-side grants are refused.';
