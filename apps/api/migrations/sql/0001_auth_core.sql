-- ============================================================================
-- 0001_auth_core -- auth + core foundation (build-forward, minimal migration)
-- Loads clean on PostgreSQL 18.
--
-- The first step of the forward migration chain (see docs/data-architecture.md
-- section 8; the full validated north-star DDL lives in docs/proposed-schema.sql
-- as the reference this is sliced from). This migration is everything login + RLS
-- depend on and nothing else:
--   * the RLS principal roles (anon / authenticated / service_role)
--   * the `auth` schema: auth.users (identity only), auth.tenants (+ lineage trigger),
--     auth.api_keys, and the WorkOS RBAC mirror (roles / permissions /
--     role_permissions / organisation_memberships) + a seed of the environment role
--     templates and the reserved 'contacts:read_all' permission
--   * the auth.* claims/RLS helpers, incl. the client-scope helpers (can_read_all /
--     contact_scope / account_scope), the 'client' tenant_lineage lever, and the
--     SECURITY DEFINER auth.resolve_claims() the request layer calls to mint claims
--   * public.set_updated_at() + its triggers, ENABLE + FORCE RLS, policies, grants
--   * the audit spine: the `audit` schema + append-only audit.log + the generic
--     audit.record() DML trigger
--
-- See docs/identity-and-access.md for the identity/access model. Improvements over
-- the draft (see plan): auth.lineage() now recomputes descendants on re-parent;
-- auth.users read is scoped to same-tenant + self (not USING(true)); auth.resolve_claims()
-- is the request-layer claim minter. DEFERRED to later steps: the remaining WorkOS
-- mirror tables (apps / organisation_domains / sessions); pgmq queues (0003); the
-- platform/crm/integrations schemas (0002/0003).
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Supabase-style DB roles (RLS principals). service_role bypasses RLS.
-- Best-effort create (swallow duplicate / insufficient_privilege); when the
-- migrator lacks CREATEROLE the roles come from migrations/bootstrap/01-roles.sql.
DO $$ BEGIN CREATE ROLE anon NOLOGIN; EXCEPTION WHEN duplicate_object OR insufficient_privilege THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN; EXCEPTION WHEN duplicate_object OR insufficient_privilege THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE service_role NOLOGIN BYPASSRLS; EXCEPTION WHEN duplicate_object OR insufficient_privilege THEN NULL; END $$;

CREATE SCHEMA IF NOT EXISTS auth;
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;

-- ============================================================================
-- auth.users -- principal identity (one row per human/login). auth.uid() resolves
-- here. WorkOS-backed. Project-per-tenant (docs/identity-and-access.md §0): a login
-- belongs to exactly one tenant (its WorkOS project), so email is unique WITHIN the
-- tenant, not globally -- the same person at two firms is two separate logins.
-- `tenant_id` is nullable during the expand phase (the WorkOS sync backfills it; it
-- tightens to NOT NULL once provisioning/sync fully set it). FK added after
-- auth.tenants exists (below).
-- ============================================================================
CREATE TABLE auth.users (
  -- Primary Key
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  uid text NOT NULL UNIQUE,

  -- Tenancy -- the WorkOS project this login belongs to (nullable during expand).
  tenant_id int,

  -- Required Fields. Email is unique per tenant (see the table constraint below),
  -- never globally -- clients/staff of different firms never share a login.
  email text NOT NULL,

  -- Optional Fields
  first_name text,
  last_name text,
  mobile text,

  -- Auth / WorkOS fields
  auth_id text,
  workos_user_id text UNIQUE,
  invitation_id text,
  invitation_sent_at timestamptz,
  invitation_expires_at timestamptz,
  invitation_accepted_at timestamptz,
  last_sign_in timestamptz,
  avatar text,

  -- Status (invited users are created disabled until acceptance)
  enabled boolean NOT NULL DEFAULT false,

  -- Timestamps (set_updated_at() keeps updated_at honest on UPDATE)
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,

  -- Generated Fields. A STORED generated column must be IMMUTABLE -- concat_ws() is only
  -- STABLE, so build the full name with immutable ops (||/COALESCE/trim/NULLIF). A missing
  -- first/last name still doesn't NULL the whole name; both missing collapses to NULL.
  name text GENERATED ALWAYS AS (
    NULLIF(trim(both ' ' from COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')), '')
  ) STORED,

  -- Email is unique WITHIN a tenant (project-per-tenant, §0). NULL tenant_id rows are
  -- distinct under this key (expand-phase synced users) -- it tightens with NOT NULL later.
  CONSTRAINT users_tenant_email_key UNIQUE (tenant_id, email)
);

CREATE INDEX users_enabled_idx ON auth.users(enabled);
CREATE INDEX users_workos_user_id_idx ON auth.users(workos_user_id) WHERE workos_user_id IS NOT NULL;

-- ============================================================================
-- auth.tenants -- the tenant/firm entity. Self-referential hierarchy with a
-- materialised lineage[] array maintained by the triggers below. WorkOS-backed.
-- This table IS the tenant, so it is NOT tenant-scoped.
-- ============================================================================
CREATE TABLE auth.tenants (
  -- Primary Key
  id int PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  uid text NOT NULL UNIQUE,

  -- Foreign Keys
  parent_id int REFERENCES auth.tenants(id) ON DELETE SET NULL,

  -- Required Fields
  alias text NOT NULL UNIQUE,
  name text NOT NULL,

  -- Hierarchy (maintained by the auth.lineage* triggers: [self, ...ancestors])
  lineage int[],

  -- WorkOS. Project-per-tenant (docs/identity-and-access.md §0): the tenant maps to a
  -- WorkOS PROJECT; its per-stage environments live in auth.tenant_environments below.
  -- workos_organisation_id is retained transitionally as the tenant's default/portal org
  -- until the provisioning/sync rewrite moves portal orgs (Client Portal / Platform) onto
  -- the memberships. (See docs/project-context.md "Next steps".)
  workos_project_id text UNIQUE,
  workos_organisation_id text UNIQUE,

  -- Optional Fields
  website text,
  email text,
  phone text,
  domains jsonb,
  icon text[],
  logo text[],
  theme jsonb,
  frn int,
  chn text,
  localisation jsonb,
  enabled boolean DEFAULT true,

  -- The single canonical "model" tenant: a template holder with no WorkOS project. Its roles
  -- (auth.roles rows, edited mirror-only) are the preset new tenants clone on creation. Exactly
  -- one row is is_model = true; it never has logins and is hidden from the tenants list.
  is_model boolean NOT NULL DEFAULT false,

  -- Hierarchy integrity: a tenant cannot be its own parent. Multi-hop cycles
  -- (A->B->C->A) are rejected by auth.lineage() before lineage[] is materialised.
  CONSTRAINT tenants_no_self_parent CHECK (parent_id IS NULL OR parent_id <> id)
);

CREATE INDEX tenants_enabled_idx ON auth.tenants(enabled);
CREATE INDEX tenants_domains_idx ON auth.tenants USING gin (domains) WHERE domains IS NOT NULL;
CREATE INDEX tenants_parent_id_idx ON auth.tenants(parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX tenants_workos_organisation_id_idx ON auth.tenants(workos_organisation_id) WHERE workos_organisation_id IS NOT NULL;
CREATE INDEX tenants_workos_project_id_idx ON auth.tenants(workos_project_id) WHERE workos_project_id IS NOT NULL;

-- auth.users.tenant_id FK -- added here, now that auth.tenants exists. A login belongs to
-- exactly one tenant (project-per-tenant, §0); ON DELETE CASCADE so removing a tenant
-- removes its logins.
ALTER TABLE auth.users
  ADD CONSTRAINT users_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES auth.tenants(id) ON DELETE CASCADE;
CREATE INDEX users_tenant_id_idx ON auth.users(tenant_id) WHERE tenant_id IS NOT NULL;

-- ============================================================================
-- auth.tenant_environments -- the per-tenant WorkOS ENVIRONMENT registry (§0). One row
-- per (tenant, stage). Holds only the NON-SECRET routing/config the API needs to verify a
-- token against the right environment (client_id / issuer) and to resolve a portal host to
-- its tenant. The environment's api key + cookie password are set via the admin UI and stored
-- ENCRYPTED in auth.environment_secrets (below), decrypted by the API with SECRETS_MASTER_KEY.
-- A running deployment uses the environment row matching its own stage.
-- ============================================================================
CREATE TABLE auth.tenant_environments (
  id int PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  tenant_id int NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  -- One deployment ("instance") holds exactly one stage — dev / staging / prod are separate
  -- deployments (each its own DB). So a tenant has ONE environment row per instance; the stage
  -- is stamped from the instance's APP_STAGE. The column stays for record-keeping + the (unlikely)
  -- shared-instance case.
  stage text NOT NULL CHECK (stage IN ('dev', 'staging', 'production')),
  workos_client_id text,     -- non-secret; selects the JWKS used to verify this env's tokens
  issuer text,               -- the token `iss` for this environment (issuer -> tenant lookup)
  -- The tenant's client and platform portals share this WorkOS environment but are served on
  -- their own hosts; the shared portals resolve tenant -> environment by matching the request
  -- host to one of these.
  client_portal_host text,
  platform_host text,
  -- The two portal WorkOS orgs within this environment, created by the admin backend once
  -- the environment's key is set (project-per-tenant: org = portal). Members are invited
  -- into the one matching their portal (client -> client_portal, staff -> platform).
  client_portal_org_id text,
  platform_org_id text,
  -- The platform's own Internal Operations org (Graphene ops staff). Only set on the
  -- Graphene tenant's core-project environment; NULL for real firms.
  operations_org_id text,
  -- The last WorkOS Events API cursor consumed for this environment (project). NULL until the
  -- first poll, which backfills via a full reconcile then advances to the latest event.
  events_cursor text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tenant_environments_tenant_stage_key UNIQUE (tenant_id, stage)
);
CREATE INDEX tenant_environments_tenant_id_idx ON auth.tenant_environments(tenant_id);
CREATE INDEX tenant_environments_issuer_idx ON auth.tenant_environments(issuer) WHERE issuer IS NOT NULL;
CREATE INDEX tenant_environments_client_id_idx ON auth.tenant_environments(workos_client_id) WHERE workos_client_id IS NOT NULL;
CREATE INDEX tenant_environments_client_portal_host_idx ON auth.tenant_environments(client_portal_host) WHERE client_portal_host IS NOT NULL;
CREATE INDEX tenant_environments_platform_host_idx ON auth.tenant_environments(platform_host) WHERE platform_host IS NOT NULL;

-- ============================================================================
-- auth.environment_secrets -- the per-environment WorkOS api key + session cookie password,
-- Fernet-encrypted (app/crypto.py) with SECRETS_MASTER_KEY. Set via the admin UI. This table
-- is service_role-ONLY: no authenticated grant or policy exists, so even the ciphertext is
-- unreadable on the request path -- only the API (service_role) reads + decrypts it.
-- ============================================================================
CREATE TABLE auth.environment_secrets (
  id int PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  tenant_environment_id int NOT NULL UNIQUE REFERENCES auth.tenant_environments(id) ON DELETE CASCADE,
  workos_api_key_ct text,     -- Fernet ciphertext of the environment's WorkOS API key
  cookie_password_ct text,    -- Fernet ciphertext of the session cookie password
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- auth.lineage(): BEFORE INSERT OR UPDATE -- build this row's lineage[] = the tenant
-- itself plus all ancestors. e.g. tenant 3 (parent 2, parent 1) => lineage = [1, 2, 3].
CREATE OR REPLACE FUNCTION auth.lineage() RETURNS TRIGGER AS $$
  BEGIN
    -- Cycle guard: a corrupted parent chain would break every tenant-isolation
    -- policy (lineage[] underpins auth.tenant_lineage()), so reject cycles hard.
    IF NEW.parent_id IS NOT NULL THEN
      IF NEW.parent_id = NEW.id THEN
        RAISE EXCEPTION 'tenant % cannot be its own parent', NEW.id;
      END IF;
      IF EXISTS (
        WITH RECURSIVE ancestors AS (
          SELECT id, parent_id FROM auth.tenants WHERE id = NEW.parent_id
          UNION ALL
          SELECT t.id, t.parent_id FROM auth.tenants t
          JOIN ancestors a ON t.id = a.parent_id
        )
        SELECT 1 FROM ancestors WHERE id = NEW.id
      ) THEN
        RAISE EXCEPTION 'tenant hierarchy cycle: % is already an ancestor of proposed parent %',
          NEW.id, NEW.parent_id;
      END IF;
    END IF;

    NEW.lineage = (
      WITH RECURSIVE tenant_lineage AS (
        -- Anchor: start with the tenant itself (NEW values for INSERT/UPDATE)
        SELECT NEW.id AS id, NEW.parent_id AS parent_id
        UNION ALL
        -- Recursive: walk up via parent_id until the root (parent_id IS NULL)
        SELECT t.id, t.parent_id
        FROM auth.tenants t
        JOIN tenant_lineage tl ON t.id = tl.parent_id
        WHERE tl.parent_id IS NOT NULL
      )
      SELECT array_agg(id ORDER BY id) FROM tenant_lineage
    );
    RETURN NEW;
  END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = 'auth';

CREATE TRIGGER tenants_lineage_trigger BEFORE INSERT OR UPDATE ON auth.tenants
  FOR EACH ROW EXECUTE FUNCTION auth.lineage();

-- auth.lineage_descendants(): AFTER UPDATE OF parent_id -- when a tenant is re-parented,
-- every descendant's materialised lineage[] is now stale (the BEFORE trigger only fixes
-- the row that changed). Recompute each descendant by walking its live parent_id chain to
-- the root. We UPDATE the lineage column only (NOT parent_id), so this AFTER-OF-parent_id
-- trigger does not re-fire on the descendants -> no recursion. The descendants' BEFORE
-- trigger recomputes the same value harmlessly.
CREATE OR REPLACE FUNCTION auth.lineage_descendants() RETURNS TRIGGER AS $$
  BEGIN
    WITH RECURSIVE subtree AS (
      -- the re-parented node + everything beneath it (excluding self, handled below)
      SELECT id FROM auth.tenants WHERE parent_id = NEW.id
      UNION ALL
      SELECT t.id FROM auth.tenants t JOIN subtree s ON t.parent_id = s.id
    )
    UPDATE auth.tenants tgt
    SET lineage = (
      WITH RECURSIVE up AS (
        SELECT tgt.id AS id, tgt.parent_id AS parent_id
        UNION ALL
        SELECT t.id, t.parent_id
        FROM auth.tenants t JOIN up ON t.id = up.parent_id
        WHERE up.parent_id IS NOT NULL
      )
      SELECT array_agg(id ORDER BY id) FROM up
    )
    WHERE tgt.id IN (SELECT id FROM subtree);
    RETURN NULL;  -- AFTER trigger: return value ignored
  END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = 'auth';

CREATE TRIGGER tenants_lineage_descendants_trigger AFTER UPDATE OF parent_id ON auth.tenants
  FOR EACH ROW WHEN (OLD.parent_id IS DISTINCT FROM NEW.parent_id)
  EXECUTE FUNCTION auth.lineage_descendants();

-- ============================================================================
-- auth.api_keys -- the one authoritative credential in auth (Graphene issues it).
-- Tenant-scoped; optionally user-linked. Only a SHA-256 hash + prefix + scopes are
-- stored; the raw secret is shown once at creation.
-- ============================================================================
CREATE TABLE auth.api_keys (
  -- Primary Key
  id int PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  uid text NOT NULL UNIQUE,   -- public-facing id (e.g. 'key_...'); the int id stays internal

  -- Foreign Keys
  tenant_id int NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,  -- NULL => tenant service key

  -- Required Fields
  name text NOT NULL,
  key_prefix text NOT NULL,   -- e.g. 'gpk_live_ab12' (display + fast lookup)
  key_hash text NOT NULL,     -- SHA-256 of the secret; never the secret itself

  -- Optional Fields
  scopes text[] NOT NULL DEFAULT '{}',   -- permission slugs this key may exercise
  last_used_at timestamptz,
  expires_at timestamptz,
  revoked_at timestamptz,
  revoked_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,

  -- Timestamps
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,

  CONSTRAINT api_keys_key_hash_key UNIQUE (key_hash)
);

CREATE INDEX api_keys_tenant_id_idx ON auth.api_keys(tenant_id);
CREATE INDEX api_keys_user_id_idx ON auth.api_keys(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX api_keys_key_prefix_idx ON auth.api_keys(key_prefix);

-- ============================================================================
-- RBAC -- roles / permissions / role_permissions / organisation_memberships (see
-- docs/identity-and-access.md section 4). THIS APP is the system of record, EXCLUSIVELY: a
-- role's permissions are written here directly (app/rbac.py::set_org_role_permissions) and are
-- deliberately NEVER pushed to WorkOS -- WorkOS embeds a role's assigned permissions straight
-- into every access token it mints, which is what pushed some sealed session cookies past the
-- browser's cookie ceiling. Role OBJECTS still get created in WorkOS (role/roles ride the token
-- harmlessly); only permission assignment stopped. Runtime authz reads THESE TABLES on every
-- request (auth.member_permissions, called from app/auth.py::get_current_principal), not a JWT
-- claim -- see that function's docstring for why the JWT's embedded copy was dropped (0093).
-- ============================================================================

-- roles -- tenant-scoped and configurable. Every role belongs to a tenant (tenant_id set);
-- there are no global templates. New tenants clone the model tenant's roles; the model is
-- configured in the admin UI. "See all vs own book" is NOT hardcoded -- it is the
-- 'contacts:read_all' permission, granted to whichever roles a tenant chooses.
CREATE TABLE auth.roles (
  id int PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  workos_role_id text UNIQUE,                         -- WorkOS SoR id (NULL for local-only / pre-sync)
  tenant_id int REFERENCES auth.tenants(id) ON DELETE CASCADE,  -- NULL => environment/global template
  slug text NOT NULL,                                 -- 'adviser' | 'paraplanner' | 'client' | ...
  name text NOT NULL,
  type text NOT NULL DEFAULT 'organisation' CHECK (type IN ('environment', 'organisation')),
  -- Which portal this role governs: 'client' (Client Portal end-users), 'platform' (firm
  -- staff), or 'operations' (Graphene internal-ops staff, on the Graphene tenant only). Lets
  -- the admin RBAC UI separate them. NULL until known.
  portal text CHECK (portal IN ('client', 'platform', 'operations')),
  description text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  -- NULLS NOT DISTINCT so environment templates (tenant_id NULL) are unique by slug --
  -- without it Postgres treats NULL tenant_id as distinct and would allow duplicates.
  CONSTRAINT roles_slug_scope_key UNIQUE NULLS NOT DISTINCT (tenant_id, slug)
);
CREATE INDEX roles_tenant_id_idx ON auth.roles(tenant_id);

CREATE TABLE auth.permissions (
  id int PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  workos_permission_id text UNIQUE,
  slug text NOT NULL UNIQUE,                           -- canonical 'resource:action', e.g. 'contacts:read_all'
  name text NOT NULL,
  resource text,
  action text,
  -- Which portal this permission belongs to: 'client', 'platform' or 'operations'. Groups the RBAC UI.
  portal text CHECK (portal IN ('client', 'platform', 'operations')),
  description text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth.role_permissions (
  role_id int NOT NULL REFERENCES auth.roles(id) ON DELETE CASCADE,
  permission_id int NOT NULL REFERENCES auth.permissions(id) ON DELETE CASCADE,
  PRIMARY KEY (role_id, permission_id)
);
CREATE INDEX role_permissions_permission_id_idx ON auth.role_permissions(permission_id);

-- organisation_memberships -- one row per (user, tenant, portal); the membership's role is
-- the user's role IN that firm's portal. The active org in the token picks which membership's
-- role/permissions apply. A user may have many: across firms (adviser-at-A + client-at-B) AND
-- across portals within one firm (a firm principal who is also a client => a Platform row and
-- a Client row). `portal` = which of the firm's WorkOS portal orgs this membership lives in.
CREATE TABLE auth.organisation_memberships (
  id int PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
  workos_membership_id text UNIQUE,
  user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  tenant_id int NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  role_id int REFERENCES auth.roles(id) ON DELETE SET NULL,
  portal text NOT NULL DEFAULT 'platform' CHECK (portal IN ('client', 'platform', 'operations')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'pending')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT organisation_memberships_user_tenant_portal_key UNIQUE (user_id, tenant_id, portal)
);
CREATE INDEX organisation_memberships_user_id_idx ON auth.organisation_memberships(user_id);
CREATE INDEX organisation_memberships_tenant_id_idx ON auth.organisation_memberships(tenant_id);
CREATE INDEX organisation_memberships_role_id_idx ON auth.organisation_memberships(role_id) WHERE role_id IS NOT NULL;

-- ============================================================================
-- AUTH RLS HELPER FUNCTIONS (read claims from the WorkOS-minted JWT).
-- All STABLE; the request layer mints PRE-RESOLVED claims (auth.resolve_claims)
-- so policy evaluation does ZERO auth.* queries per row.
-- ============================================================================
CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT COALESCE(NULLIF(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $$;

-- The current principal's auth.users.id (uuid), minted as the 'user_uuid' claim.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT NULLIF(auth.jwt() ->> 'user_uuid', '')::uuid $$;

CREATE OR REPLACE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE AS $$
  SELECT COALESCE(auth.jwt() ->> 'role', 'anon') $$;

CREATE OR REPLACE FUNCTION auth.workos_org() RETURNS text LANGUAGE sql STABLE AS $$
  SELECT NULLIF(auth.jwt() ->> 'org_id', '') $$;

-- Active tenant id, PRE-RESOLVED into the claims; workos_org fallback for psql.
CREATE OR REPLACE FUNCTION auth.tenant_id() RETURNS int LANGUAGE sql STABLE AS $$
  SELECT COALESCE(
    NULLIF(auth.jwt() ->> 'tenant_id', '')::int,
    (SELECT t.id FROM auth.tenants t WHERE t.workos_organisation_id = auth.workos_org())
  ) $$;

-- The caller's tenant + all descendants. PRE-RESOLVED into the 'tenant_ids' claim;
-- falls back to the descendant scan only when the claim is absent (psql session).
-- SAFETY LEVER (docs/identity-and-access.md section 5): a 'client' principal has an
-- EMPTY lineage, so it fails every `tenant_id = ANY(auth.tenant_lineage())` staff
-- policy automatically -- a client is reachable ONLY via the explicit contact/account
-- scoped policies. Client end-users are firm-scoped by their grants, never membership.
CREATE OR REPLACE FUNCTION auth.tenant_lineage() RETURNS int[] LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN auth.role() = 'client' THEN '{}'::int[]
    WHEN auth.jwt() ? 'tenant_ids'
      THEN ARRAY(SELECT jsonb_array_elements_text(auth.jwt() -> 'tenant_ids')::int)
    ELSE COALESCE(
      (SELECT array_agg(id) FROM auth.tenants WHERE auth.tenant_id() = ANY(lineage)),
      '{}'::int[])
  END $$;

-- Granted permission slugs from the WorkOS access token ('permissions' claim).
CREATE OR REPLACE FUNCTION auth.jwt_permissions() RETURNS text[] LANGUAGE sql STABLE AS $$
  SELECT COALESCE(ARRAY(SELECT jsonb_array_elements_text(auth.jwt() -> 'permissions')), '{}'::text[]) $$;

-- Graphene platform staff: an environment-level system/admin role in the token.
CREATE OR REPLACE FUNCTION auth.is_system() RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT (auth.jwt() ->> 'role') IN ('system', 'admin')
      OR ('system' = ANY(auth.jwt_permissions())) $$;

CREATE OR REPLACE FUNCTION auth.has_role(p_role text) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT auth.is_system() OR (auth.jwt() ->> 'role') = p_role $$;

CREATE OR REPLACE FUNCTION auth.has_permission(p_perm text) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT auth.is_system() OR (p_perm = ANY(auth.jwt_permissions())) $$;

CREATE OR REPLACE FUNCTION auth.session_id() RETURNS text LANGUAGE sql STABLE AS $$
  SELECT NULLIF(auth.jwt() ->> 'sid', '') $$;

-- ---- CLIENT/ADVISER SCOPE (docs/identity-and-access.md section 5) ----
-- "See all the firm's clients" is a tenant-configurable PERMISSION, not a role check
-- in SQL: a tenant grants 'contacts:read_all' to whichever roles it wants. is_system()
-- implies it.
CREATE OR REPLACE FUNCTION auth.can_read_all() RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT auth.has_permission('contacts:read_all') $$;

-- The contacts/accounts this principal may see when NOT read-all -- PRE-RESOLVED by
-- the request layer into the 'contact_ids'/'account_ids' claims. FAIL-CLOSED: absent
-- claim => empty scope (a client sees nothing, never the firm), so the app MUST mint them.
CREATE OR REPLACE FUNCTION auth.contact_scope() RETURNS int[] LANGUAGE sql STABLE AS $$
  SELECT CASE WHEN auth.jwt() ? 'contact_ids'
    THEN ARRAY(SELECT jsonb_array_elements_text(auth.jwt() -> 'contact_ids')::int)
    ELSE '{}'::int[] END $$;

CREATE OR REPLACE FUNCTION auth.account_scope() RETURNS int[] LANGUAGE sql STABLE AS $$
  SELECT CASE WHEN auth.jwt() ? 'account_ids'
    THEN ARRAY(SELECT jsonb_array_elements_text(auth.jwt() -> 'account_ids')::int)
    ELSE '{}'::int[] END $$;

-- ============================================================================
-- auth.resolve_claims() -- the request-layer claim minter (docs/identity-and-access.md
-- section 6). The API calls this ONCE per request (see apps/api/app/db.py::tenant_session)
-- to resolve the WorkOS identity into the pre-resolved internal claims the RLS helpers
-- read. SECURITY DEFINER so it runs regardless of the caller's role/claims and is NOT
-- subject to the scoped auth.users read policy below -- this is what avoids the
-- chicken-and-egg of "you must read auth.users to mint the claims that let you read
-- auth.users". The app merges the JWT-derived half (sub/role/permissions/sid/org_id).
--
-- In 0001 contact_ids/account_ids are always empty arrays (the crm/platform tables they
-- derive from do not exist yet); 0002 does CREATE OR REPLACE to fill the client scope.
-- ============================================================================
CREATE OR REPLACE FUNCTION auth.resolve_claims(p_workos_user_id text, p_workos_org_id text)
  RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = 'auth' AS $fn$
DECLARE
  v_user_uuid uuid;
  v_tenant_id int;
  v_tenant_ids int[];
BEGIN
  -- Project-per-tenant (§0): the tenant is a property of the LOGIN, not the active org
  -- (which now denotes the portal -- Client Portal vs Platform). Resolve the user and
  -- take its tenant directly. p_workos_org_id is retained only as a transitional fallback
  -- for logins not yet backfilled with tenant_id (and for psql sessions).
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

  RETURN jsonb_build_object(
    'user_uuid',   v_user_uuid,
    'tenant_id',   v_tenant_id,
    'tenant_ids',  to_jsonb(v_tenant_ids),
    'contact_ids', '[]'::jsonb,   -- filled in 0002
    'account_ids', '[]'::jsonb    -- filled in 0002
  );
END;
$fn$;

GRANT EXECUTE ON FUNCTION auth.resolve_claims(text, text) TO authenticated, service_role;

-- ============================================================================
-- HONEST updated_at -- one trigger function, attached (by the loop below) to every
-- auth table that has an updated_at column: auth.users, roles, permissions,
-- organisation_memberships. (tenants / api_keys / role_permissions have none.)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.set_updated_at() RETURNS trigger
  LANGUAGE plpgsql AS $fn$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$fn$;

-- ============================================================================
-- ROW LEVEL SECURITY -- auth tables.
-- auth.tenants reads with USING (true): a principal can see the tenant directory,
-- AND scoping it would recurse (auth.tenant_id()/tenant_lineage() read it). auth.users
-- is SCOPED to same-tenant + self (no cross-tenant user directory leak). Writes to
-- users/tenants are service_role-only (no authenticated write policy -> denied).
-- auth.api_keys is a strict tenant-only table.
-- ============================================================================
ALTER TABLE auth.users ENABLE ROW LEVEL SECURITY;
-- A principal sees their OWN row, plus any user who is a member of a tenant in their
-- lineage (staff see their firm; a client -- empty lineage -- sees only themselves).
CREATE POLICY auth_users_scoped_read ON auth.users FOR SELECT TO authenticated USING (
  id = auth.uid()
  OR EXISTS (
    SELECT 1 FROM auth.organisation_memberships m
    WHERE m.user_id = auth.users.id
      AND m.tenant_id = ANY (auth.tenant_lineage())
  )
);
GRANT SELECT ON auth.users TO authenticated;   -- writes are service_role only (WorkOS sync)
GRANT ALL ON auth.users TO service_role;

ALTER TABLE auth.tenants ENABLE ROW LEVEL SECURITY;
CREATE POLICY auth_tenants_auth_read ON auth.tenants FOR SELECT TO authenticated USING (true);
GRANT SELECT ON auth.tenants TO authenticated;  -- writes are service_role only (WorkOS sync)
GRANT ALL ON auth.tenants TO service_role;

-- tenant_environments: the routing registry. A firm may read its own environments;
-- writes are service_role only (the operator runbook / admin backend seeds them).
ALTER TABLE auth.tenant_environments ENABLE ROW LEVEL SECURITY;
CREATE POLICY auth_tenant_environments_read ON auth.tenant_environments FOR SELECT TO authenticated USING (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT ON auth.tenant_environments TO authenticated;
GRANT ALL ON auth.tenant_environments TO service_role;

-- environment_secrets: service_role ONLY. No authenticated policy or grant exists, so the
-- request role cannot read even the ciphertext; only the API (service_role) reads/decrypts.
ALTER TABLE auth.environment_secrets ENABLE ROW LEVEL SECURITY;
GRANT ALL ON auth.environment_secrets TO service_role;

ALTER TABLE auth.api_keys ENABLE ROW LEVEL SECURITY;
CREATE POLICY auth_api_keys_tenant_read ON auth.api_keys FOR SELECT TO authenticated USING (tenant_id = ANY (auth.tenant_lineage()));
CREATE POLICY auth_api_keys_tenant_write ON auth.api_keys FOR ALL TO authenticated USING (tenant_id = ANY (auth.tenant_lineage())) WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT, INSERT, UPDATE, DELETE ON auth.api_keys TO authenticated;
GRANT ALL ON auth.api_keys TO service_role;

-- WorkOS RBAC mirror -- READ to authenticated (scoped), WRITE service_role ONLY (a
-- sync read-cache; only the WorkOS sync worker mutates it).
ALTER TABLE auth.roles ENABLE ROW LEVEL SECURITY;
CREATE POLICY auth_roles_read ON auth.roles FOR SELECT TO authenticated USING (tenant_id IS NULL OR tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT ON auth.roles TO authenticated;
GRANT ALL ON auth.roles TO service_role;

ALTER TABLE auth.permissions ENABLE ROW LEVEL SECURITY;
CREATE POLICY auth_permissions_read ON auth.permissions FOR SELECT TO authenticated USING (true);
GRANT SELECT ON auth.permissions TO authenticated;
GRANT ALL ON auth.permissions TO service_role;

ALTER TABLE auth.role_permissions ENABLE ROW LEVEL SECURITY;
CREATE POLICY auth_role_permissions_read ON auth.role_permissions FOR SELECT TO authenticated USING (true);
GRANT SELECT ON auth.role_permissions TO authenticated;
GRANT ALL ON auth.role_permissions TO service_role;

-- A firm sees its own memberships; a user always sees their OWN (the org switcher).
ALTER TABLE auth.organisation_memberships ENABLE ROW LEVEL SECURITY;
CREATE POLICY auth_org_memberships_read ON auth.organisation_memberships FOR SELECT TO authenticated USING (tenant_id = ANY (auth.tenant_lineage()) OR user_id = auth.uid());
GRANT SELECT ON auth.organisation_memberships TO authenticated;
GRANT ALL ON auth.organisation_memberships TO service_role;

-- ---- Reference seed (docs/identity-and-access.md section 4). Runs BEFORE the FORCE RLS
-- loop below, so the owner (migrator) can insert. ----
-- Permissions are NOT seeded here: the canonical catalogue lives in `app/permissions.py` and is
-- upserted into `auth.permissions` by `permissions.sync_permissions()` on app startup (and in the
-- test fixture). That is the single source of truth (read/write per resource + `system`); adding a
-- permission is a one-line change there, no migration. RLS policies still reference specific slugs
-- by literal (e.g. `auth.is_reserved()` → `contacts:read_all`); the row is guaranteed by the sync.

-- The canonical model tenant (blank). Its roles are the preset new tenants clone.
INSERT INTO auth.tenants (uid, alias, name, is_model) VALUES
  ('ten_model', 'model', 'Model Tenant', true)
ON CONFLICT (alias) DO NOTHING;

GRANT USAGE ON ALL SEQUENCES IN SCHEMA auth TO authenticated, service_role;

-- Attach set_updated_at to every auth table with an updated_at column.
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.table_schema, c.table_name
    FROM information_schema.columns c
    JOIN information_schema.tables t
      ON t.table_schema = c.table_schema AND t.table_name = c.table_name
    WHERE c.column_name = 'updated_at'
      AND t.table_type = 'BASE TABLE'
      AND c.table_schema = 'auth'
  LOOP
    EXECUTE format(
      'CREATE TRIGGER set_updated_at BEFORE UPDATE ON %I.%I '
      'FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()',
      r.table_schema, r.table_name);
  END LOOP;
END $$;

-- FORCE RLS so even the table OWNER (the migrator role) is subject to policies.
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT schemaname, tablename FROM pg_tables WHERE schemaname = 'auth'
  LOOP
    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', r.schemaname, r.tablename);
  END LOOP;
END $$;

-- ============================================================================
-- AUDIT SPINE -- audit.log is cross-cutting foundation, so it lives in core:
-- auth.users / auth.tenants / auth.api_keys changes are themselves auditable, and
-- every later migration's domain writes land in this one table. Append-only to the
-- app role (tamper-evident FCA evidence).
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS audit;
GRANT USAGE ON SCHEMA audit TO anon, authenticated, service_role;

CREATE TABLE audit.log (
  -- Primary Key
  id bigint PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,

  -- Foreign Keys
  tenant_id int REFERENCES auth.tenants(id) ON DELETE CASCADE,
  user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL,

  -- Required Fields
  action text NOT NULL,

  -- Optional Fields
  entity_type text,
  entity_id bigint,
  detail jsonb,

  -- Timestamps
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX log_entity_idx ON audit.log(entity_type, entity_id);
CREATE INDEX log_tenant_id_idx ON audit.log(tenant_id) WHERE tenant_id IS NOT NULL;
CREATE INDEX log_user_id_idx ON audit.log(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX log_created_at_idx ON audit.log(created_at);

ALTER TABLE audit.log ENABLE ROW LEVEL SECURITY;
-- READ is tenant-scoped (a firm sees its own trail; system sees firm-less rows too).
CREATE POLICY audit_log_read ON audit.log FOR SELECT TO authenticated USING (tenant_id = ANY (auth.tenant_lineage()) OR (tenant_id IS NULL AND auth.is_system()));
-- APPEND must NEVER be blocked by RLS -- evidence-writing cannot fail because of tenant
-- scoping (the DML trigger stamps the AFFECTED row's tenant, which may differ from the
-- actor's during a cross-tenant admin action). Append-only is kept by the grant
-- (SELECT, INSERT only -- no UPDATE/DELETE to the app role).
CREATE POLICY audit_log_append ON audit.log FOR INSERT TO authenticated WITH CHECK (true);
GRANT SELECT, INSERT ON audit.log TO authenticated;  -- append-only (FCA evidence)
GRANT ALL ON audit.log TO service_role;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit TO authenticated, service_role;

ALTER TABLE audit.log FORCE ROW LEVEL SECURITY;

-- ============================================================================
-- audit.record() -- the generic DML audit trigger (two-tier audit, data-architecture.md
-- Decision 5 C2). DB-level trigger that captures EVERY write to a sensitive table,
-- including out-of-band ones (workers, psql, migrations) that app-level events cannot
-- see. Actor + tenant come from the request claims (auth.uid()/auth.tenant_id()).
-- SECURITY DEFINER + the append-always policy mean the write never fails; secrets redacted.
-- ============================================================================
CREATE OR REPLACE FUNCTION audit.record() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = 'audit', 'auth', 'pg_catalog' AS $fn$
DECLARE
  v_new     jsonb := CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END;
  v_old     jsonb := CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END;
  v_subject jsonb := COALESCE(v_new, v_old);
  -- never copy secrets / opaque pointers into the trail (key_hash is a hash, not the
  -- secret, but it stays out of logs on principle; credentials_ref points at a vault).
  v_redact  text[] := ARRAY['key_hash', 'credentials_ref', 'workos_api_key_ct', 'cookie_password_ct'];
  v_entity  bigint;
  v_tenant  int;
  v_detail  jsonb;
  k         text;
BEGIN
  -- entity_id only when the PK is integer-shaped; uuid/composite PKs live in detail.
  BEGIN v_entity := (v_subject ->> 'id')::bigint; EXCEPTION WHEN others THEN v_entity := NULL; END;

  -- Stamp the AFFECTED row's tenant (correct for cross-tenant admin writes); fall back
  -- to the actor's tenant for tables that carry no tenant_id (e.g. auth.*).
  BEGIN
    v_tenant := COALESCE(NULLIF(v_subject ->> 'tenant_id', '')::int, auth.tenant_id());
  EXCEPTION WHEN others THEN v_tenant := auth.tenant_id();
  END;

  v_new := v_new - v_redact;
  v_old := v_old - v_redact;

  IF TG_OP = 'UPDATE' THEN
    v_detail := '{}'::jsonb;   -- only the columns that actually changed (redacted ones excluded)
    FOR k IN SELECT jsonb_object_keys(v_new) LOOP
      IF v_new -> k IS DISTINCT FROM v_old -> k THEN
        v_detail := v_detail || jsonb_build_object(k, jsonb_build_object('old', v_old -> k, 'new', v_new -> k));
      END IF;
    END LOOP;
    v_detail := jsonb_build_object('id', v_subject -> 'id', 'changed', v_detail);
  ELSIF TG_OP = 'INSERT' THEN
    v_detail := jsonb_build_object('id', v_subject -> 'id', 'row', v_new);
  ELSE
    v_detail := jsonb_build_object('id', v_subject -> 'id', 'row', v_old);
  END IF;

  INSERT INTO audit.log (tenant_id, user_id, action, entity_type, entity_id, detail)
  VALUES (
    v_tenant,
    auth.uid(),
    TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME || '.' || lower(TG_OP),
    TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
    v_entity,
    v_detail
  );
  RETURN NULL;  -- AFTER trigger: return value is ignored
END;
$fn$;

-- audit.attach(text[]) -- idempotently put the audit_row trigger on each table. Each
-- migration calls this for the sensitive tables it introduces (CREATE TRIGGER has no
-- IF NOT EXISTS, so we guard on pg_trigger).
CREATE OR REPLACE FUNCTION audit.attach(p_tables text[]) RETURNS void
  LANGUAGE plpgsql AS $fn$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY p_tables LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_trigger
      WHERE tgrelid = t::regclass AND tgname = 'audit_row' AND NOT tgisinternal
    ) THEN
      EXECUTE format(
        'CREATE TRIGGER audit_row AFTER INSERT OR UPDATE OR DELETE ON %s '
        'FOR EACH ROW EXECUTE FUNCTION audit.record()', t);
    END IF;
  END LOOP;
END;
$fn$;

-- Sensitive auth tables: identity, the tenant directory, API-key issuance, and the
-- RBAC mirror (role grants + org memberships are security-relevant changes).
SELECT audit.attach(ARRAY[
  'auth.users', 'auth.tenants', 'auth.tenant_environments', 'auth.environment_secrets',
  'auth.api_keys', 'auth.roles', 'auth.organisation_memberships'
]);

-- ============================================================================
-- BYPASSRLS ownership for the cross-RLS SECURITY DEFINER helpers.
-- FORCE ROW LEVEL SECURITY binds even the table owner (the migrator), so a definer
-- function owned by the migrator would itself see/insert nothing. Reassign the helpers
-- that must cross RLS to service_role (BYPASSRLS): resolve_claims (reads auth.users/
-- tenants to mint claims), the lineage triggers (walk the tenant tree), and audit.record
-- (must always be able to append). set_updated_at / audit.attach need no bypass.
-- (FORCE RLS stays meaningful for the migrator itself -- it is NOT BYPASSRLS; only these
-- functions, executing AS service_role, bypass.)
-- ============================================================================
GRANT CREATE ON SCHEMA auth, audit TO service_role;  -- the new owner needs CREATE on the schema
ALTER FUNCTION auth.lineage()              OWNER TO service_role;
ALTER FUNCTION auth.lineage_descendants()  OWNER TO service_role;
ALTER FUNCTION auth.resolve_claims(text, text) OWNER TO service_role;
ALTER FUNCTION audit.record()              OWNER TO service_role;
