-- ============================================================================
-- 0030_auth_directory_scope.sql — close the cross-tenant leaks in the auth directory.
--
-- Three tables in `auth` still read with USING (true) from 0001, which is a genuine
-- cross-tenant leak in a product whose central promise is tenant isolation:
--
--   1. auth.tenants (0001:577) — ANY authenticated principal, INCLUDING a client end-user of
--      one firm, could enumerate every firm on the instance: name, alias, uid, website,
--      workos_project_id, workos_organisation_id and the whole white-label `theme` jsonb
--      (i.e. every competitor firm's branding). The comment there gives the real reason it was
--      left open: the RLS helpers themselves read auth.tenants, so a naive scoped policy
--      recurses (`auth.tenant_id()` / `auth.tenant_lineage()` fall back to scanning the table
--      when the pre-resolved claims are absent — the psql / no-claims path).
--
--      Fix without reintroducing recursion: move BOTH fallback scans into SECURITY DEFINER
--      accessors owned by service_role (BYPASSRLS) — the same device 0001 already uses for
--      auth.resolve_claims / audit.record — then scope the table itself. The policy's own
--      predicate therefore never re-enters auth.tenants under RLS, whether or not the claims
--      carry tenant_id/tenant_ids.
--
--      Row scoping is the whole fix: a session now sees its OWN firm (+ descendants for a
--      parent-firm staffer) and nothing else. We deliberately do NOT column-restrict the grant
--      — every remaining column is the caller's own firm's, the WorkOS ids are non-secret
--      pointers the portal already holds from /internal/env-config, and the actual secrets live
--      in auth.environment_secrets, which `authenticated` cannot read at all (no policy, no grant).
--      A column-list grant would instead break any future `SELECT *` at runtime.
--
--   2. auth.permissions (0001:606) — the catalogue. Not tenant data (it also ships to browsers
--      via @graphene/api-client/permissions), but there is no reason for a request session to
--      enumerate slugs no role of its firm holds. Scoped to "granted by a role in my lineage,
--      or held in my own token".
--
--   3. auth.role_permissions (0001:611) — the GRANT GRAPH. The row carries no tenant column, so
--      USING (true) exposed every firm's role→permission mapping to every session. Scoped
--      through auth.roles exactly like auth_roles_read.
--
-- All three tables' reads today come from service_role sessions (admin_session / firm_session /
-- internal_session — app/rbac.py, app/environments.py, app/directory.py, app/branding.py,
-- app/provisioning.py), which are BYPASSRLS. The only `authenticated`-role readers of
-- auth.tenants are GET /me/tenant (`WHERE id = auth.tenant_id()`) and GET /me/memberships
-- (joined off the caller's own memberships) — both already own-tenant only, so nothing in app/
-- or the portals depends on reading another tenant's row.
--
-- Also (docs/audit/05-admin.md): auth.role_permissions is UNAUDITED. `audit.attach` covers
-- auth.roles and auth.organisation_memberships but not the grants themselves, so the single most
-- security-sensitive configuration action in the product — granting a role firm-wide client
-- visibility — left no trace in audit.log at all (and rbac.set_org_role_permissions
-- delete-then-reinserts, so there is nothing else to reconstruct it from). Attach the spine.
-- auth.permissions is deliberately left unattached: sync_permissions() upserts the whole
-- registry on every app startup, which would add a no-change UPDATE row per permission per boot.
--
--   4. auth.api_keys (0001:594-596) — `authenticated` held INSERT/UPDATE/DELETE plus a FOR ALL
--      policy whose only predicate was the tenant lineage. No endpoint exposes it, so it is
--      latent, but any tenant session that reaches SQL could mint a credential for its own firm
--      with no permission gate at all. Keys are issued by Graphene (service_role); the request
--      role has no business writing them. Read stays (a firm listing its own keys is legitimate).
--
-- lock_timeout: policy/function replacement takes short ACCESS EXCLUSIVE locks; fail fast rather
-- than queue behind a long transaction (repo convention, cf. 0024).
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- ============================================================================
-- Recursion-safe accessors for the two auth.tenants scans the RLS helpers perform.
-- SECURITY DEFINER + owned by service_role (BYPASSRLS) so they are NOT subject to the scoped
-- policy below — this is what avoids "you must read auth.tenants to evaluate the policy that
-- lets you read auth.tenants". Both are STABLE and only ever touch auth.tenants.
--
-- CONTAINMENT IS PART OF THE CONTRACT, not an optional extra. A BYPASSRLS definer function that
-- takes a caller-supplied argument and returns tenant data is a cross-tenant ORACLE: closing the
-- table read while leaving `SELECT auth.tenant_lineage_of(<any id>)` open would just move the leak
-- to the function boundary for the same principal (a client-portal session could enumerate another
-- firm's descendant tree, or resolve any WorkOS org id to a tenant id). Revoking EXECUTE is NOT
-- sufficient on its own either — CREATE FUNCTION grants EXECUTE to PUBLIC by default, and
-- `authenticated` genuinely needs EXECUTE because the SECURITY INVOKER helpers below call these
-- as the caller. So each function guards its own argument internally and fails closed
-- (NULL / '{}') for anything outside the caller's own resolved tenant. Graphene ops (is_system)
-- keeps the directory view, matching the policies below.
--
-- The only in-tree callers are auth.tenant_id() / auth.tenant_lineage() (and the flows policy in
-- 0031), each of which passes the caller's OWN org / tenant — so the guards are transparent to
-- every legitimate use. Proven by tests/test_rls_directory.py.
-- ============================================================================
CREATE OR REPLACE FUNCTION auth.tenant_by_workos_org(p_org text) RETURNS int
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = 'auth', 'pg_catalog' AS $$
  SELECT t.id FROM auth.tenants t
   WHERE p_org IS NOT NULL
     -- Containment: you may resolve your OWN org, nothing else.
     AND (p_org = auth.workos_org() OR auth.is_system())
     AND t.workos_organisation_id = p_org $$;

CREATE OR REPLACE FUNCTION auth.tenant_lineage_of(p_tenant_id int) RETURNS int[]
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = 'auth', 'pg_catalog' AS $$
  -- Containment: the descendant tree of YOUR OWN resolved tenant, nothing else. Note the guard
  -- reads auth.tenant_id(), which resolves from the claims (or via the guarded accessor above),
  -- so it never re-enters the auth.tenants policy.
  SELECT CASE
    WHEN p_tenant_id IS NULL THEN '{}'::int[]
    WHEN p_tenant_id = auth.tenant_id() OR auth.is_system() THEN COALESCE(
      (SELECT array_agg(t.id) FROM auth.tenants t WHERE p_tenant_id = ANY (t.lineage)),
      '{}'::int[])
    ELSE '{}'::int[]
  END $$;

ALTER FUNCTION auth.tenant_by_workos_org(text) OWNER TO service_role;
ALTER FUNCTION auth.tenant_lineage_of(int)     OWNER TO service_role;
REVOKE ALL ON FUNCTION auth.tenant_by_workos_org(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION auth.tenant_lineage_of(int)     FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.tenant_by_workos_org(text) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION auth.tenant_lineage_of(int)     TO authenticated, service_role;

-- auth.tenant_id() (0001) — claim first, definer accessor as the psql fallback. Behaviour is
-- unchanged; only the fallback's privilege changes (COALESCE argument evaluation order is not a
-- guarantee we want to rest tenant isolation on).
CREATE OR REPLACE FUNCTION auth.tenant_id() RETURNS int LANGUAGE sql STABLE AS $$
  SELECT COALESCE(
    NULLIF(auth.jwt() ->> 'tenant_id', '')::int,
    auth.tenant_by_workos_org(auth.workos_org())
  ) $$;

-- auth.tenant_lineage() (0004 body, verbatim except the ELSE branch) — the client-portal safety
-- lever and the pre-resolved-claims path are untouched.
CREATE OR REPLACE FUNCTION auth.tenant_lineage() RETURNS int[] LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN auth.portal() = 'client' OR auth.role() = 'client' THEN '{}'::int[]
    WHEN auth.jwt() ? 'tenant_ids'
      THEN ARRAY(SELECT jsonb_array_elements_text(auth.jwt() -> 'tenant_ids')::int)
    ELSE auth.tenant_lineage_of(auth.tenant_id())
  END $$;

-- ============================================================================
-- auth.tenants — scoped read. A session sees its own firm (the resolved tenant, which is what a
-- client-portal principal has: its lineage is deliberately empty) plus the descendants a
-- parent-firm staffer reaches. Graphene ops (is_system) keeps the directory view; the admin
-- surface itself runs on service_role anyway. Writes remain service_role-only (no write policy).
-- ============================================================================
DROP POLICY auth_tenants_auth_read ON auth.tenants;
CREATE POLICY auth_tenants_scoped_read ON auth.tenants FOR SELECT TO authenticated USING (
  id = auth.tenant_id()
  OR id = ANY (auth.tenant_lineage())
  OR auth.is_system()
);

-- ============================================================================
-- auth.permissions — the catalogue, scoped to what this firm's roles actually grant (plus what
-- the caller's own token carries). Cross-firm slugs are no longer enumerable from a request
-- session; the full catalogue for the RBAC editor is read on service_role (rbac.list_permissions).
-- ============================================================================
DROP POLICY auth_permissions_read ON auth.permissions;
CREATE POLICY auth_permissions_read ON auth.permissions FOR SELECT TO authenticated USING (
  auth.is_system()
  OR slug = ANY (auth.jwt_permissions())
  OR EXISTS (
    SELECT 1 FROM auth.role_permissions rp
    JOIN auth.roles r ON r.id = rp.role_id
    WHERE rp.permission_id = auth.permissions.id
      AND (r.tenant_id IS NULL OR r.tenant_id = ANY (auth.tenant_lineage()))
  )
);

-- ============================================================================
-- auth.role_permissions — the grant graph, scoped through the role's tenant (mirrors
-- auth_roles_read: environment/global templates have tenant_id NULL and stay readable).
-- ============================================================================
DROP POLICY auth_role_permissions_read ON auth.role_permissions;
CREATE POLICY auth_role_permissions_read ON auth.role_permissions FOR SELECT TO authenticated USING (
  auth.is_system()
  OR EXISTS (
    SELECT 1 FROM auth.roles r
    WHERE r.id = auth.role_permissions.role_id
      AND (r.tenant_id IS NULL OR r.tenant_id = ANY (auth.tenant_lineage()))
  )
);

-- ============================================================================
-- auth.api_keys — take the write path away from the request role. 0001 granted
-- INSERT/UPDATE/DELETE to `authenticated` behind a FOR ALL policy whose only predicate was the
-- tenant lineage, i.e. any tenant session could mint itself an API key with no permission gate.
-- Nothing in app/ writes this table under `authenticated` (keys are a Graphene-issued credential;
-- provisioning runs on service_role, which keeps ALL), so this is a pure removal of latent
-- surface. SELECT and the tenant-scoped read policy stay — a firm listing its own keys is fine.
-- ============================================================================
DROP POLICY auth_api_keys_tenant_write ON auth.api_keys;
REVOKE INSERT, UPDATE, DELETE ON auth.api_keys FROM authenticated;

-- ============================================================================
-- Audit the grant graph. Every INSERT/DELETE from rbac.set_org_role_permissions,
-- provisioning.clone_model_roles and environments.push_roles_to_workos now lands in audit.log
-- (the composite PK means entity_id stays NULL; role_id/permission_id ride in `detail.row`).
-- ============================================================================
SELECT audit.attach(ARRAY['auth.role_permissions']);
