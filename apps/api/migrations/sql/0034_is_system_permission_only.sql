-- 0034 — stop trusting the token's `role` name for superuser, in SQL as well as in Python.
--
-- `auth.is_system()` (0001_auth_core.sql:458) granted on `(auth.jwt() ->> 'role') IN
-- ('system','admin')`. That role name comes from the TENANT'S OWN WorkOS environment, so any firm
-- that happened to name a role "admin" got a cross-tenant superuser: `auth.has_permission()` and
-- `auth.can_read_all()` both short-circuit on `is_system()`, which is ~50 policy call sites across
-- ten migrations. The API stopped publishing a raw role claim (app/auth.py) and the generated UI
-- gate stopped trusting it (packages/api-client/permissions.ts), but the SQL branch stayed live —
-- a footgun for any OTHER claims minter: a psql session, a future service, a direct set_config.
--
-- The `system` PERMISSION is the only superuser signal. A tenant cannot grant it to itself: the
-- registry (app/permissions.py) owns the slug and `PUT /firm/roles/{slug}/permissions` denylists it.
--
-- `auth.has_role(p_role)` keeps comparing the role claim, which is correct — asking "is this
-- principal the adviser role" is a legitimate tenant-scoped question. What it must not do is
-- inherit the superuser bypass, so the `is_system()` short-circuit comes out of it.

CREATE OR REPLACE FUNCTION auth.is_system() RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT 'system' = ANY(auth.jwt_permissions()) $$;

COMMENT ON FUNCTION auth.is_system() IS
  'Graphene platform staff. The `system` permission ONLY -- never the token''s role name, which is '
  'tenant-controlled (see 0034). Short-circuits has_permission() and can_read_all().';

CREATE OR REPLACE FUNCTION auth.has_role(p_role text) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT (auth.jwt() ->> 'role') = p_role $$;

COMMENT ON FUNCTION auth.has_role(text) IS
  'Is this principal in the named tenant role? Tenant-scoped question, no superuser bypass (0034).';
