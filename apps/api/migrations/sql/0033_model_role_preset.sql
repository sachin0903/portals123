-- ============================================================================
-- 0033_model_role_preset.sql — the bootstrap that makes a fresh install usable.
--
-- 0001_auth_core.sql:631 seeds the model tenant BLANK, and no migration or delta has ever created
-- a single auth.roles / auth.role_permissions row. Consequences on a fresh install
-- (docs/audit/06-core.md "Permission registry vs actual UI gates", 05-admin.md "Create firm"):
--
--   * provisioning.clone_model_roles clones nothing, so a newly provisioned firm has zero roles;
--   * provisioning.grant_portal_access then raises LookupError("role ... does not exist"), so the
--     firm cannot be invited into at all — there is no first admin, ever;
--   * every require_permission endpoint 403s, because no role can hold any permission.
--
-- This seeds the model tenant — the preset every new firm clones — with a sensible default role
-- set built from the slugs declared in app/permissions.py:
--
--   owner      (platform) — the firm principal: everything the firm self-serves, including
--                           contacts:read_all (firm-wide client visibility).
--   adviser    (platform) — deliberately WITHOUT contacts:read_all, so the adviser is book-scoped
--                           by RLS (auth.contact_scope()); this is the shape the book-scoping
--                           policies exist for.
--   compliance (platform) — firm-wide READ oversight: every client, document, flow, case and task,
--                           no write anywhere.
--   client     (client)   — the client-portal role. Intentionally holds NO permissions: the client
--                           surface returns only rows RLS already scopes to the caller
--                           (app/permissions.py), and grant_portal_access needs a client-portal
--                           role to exist before a client can be invited.
--
-- NON-DESTRUCTIVE: the preset is applied ONLY when the model tenant currently has no roles at all.
-- An install whose model has already been configured through the admin /model UI is left exactly
-- as it is. Re-running is therefore a no-op, which is also what makes it safe to exercise directly
-- from tests/test_schema_invariants.py.
--
-- On seeding auth.permissions here: the registry in app/permissions.py remains the single source
-- of truth (sync_permissions() upserts and REFRESHES every row on app startup, CI fails on drift).
-- But `moon run api:migrate` runs BEFORE the app ever boots, so on a fresh install auth.permissions
-- is empty at this point and the grants below would join to nothing. We therefore insert the rows
-- this preset references as migrate-time placeholders (slug + derived resource/action only); the
-- first app startup fills in the canonical name/portal/description. Nothing else is seeded, and
-- no slug is invented here — every one below is declared in app/permissions.py.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- The seed writes auth.tenants / auth.roles / auth.permissions / auth.role_permissions, all of
-- which are FORCE RLS with no write policy for the migrator; run it elevated (0023/0024 idiom).
SET LOCAL ROLE service_role;

-- The canonical model tenant (0001 seeds it too; repeated so this file stands alone).
INSERT INTO auth.tenants (uid, alias, name, is_model) VALUES
  ('ten_model', 'model', 'Model Tenant', true)
ON CONFLICT (alias) DO NOTHING;

-- Bootstrap placeholders for the permission rows the preset grants (see the header note).
INSERT INTO auth.permissions (slug, name, resource, action, portal, description)
SELECT s, s, split_part(s, ':', 1), split_part(s, ':', 2), 'platform',
       'Bootstrap placeholder; app/permissions.py is the source of truth and refreshes this row.'
  FROM unnest(ARRAY[
    'members:read', 'members:write',
    'roles:read', 'roles:write',
    'branding:read', 'branding:write',
    'documents:read', 'documents:write',
    'forms:read', 'forms:write',
    'templates:read', 'templates:write',
    'datasets:read', 'datasets:write',
    'flows:read', 'flows:write',
    'cases:read', 'cases:write',
    'tasks:read', 'tasks:write',
    'contacts:read', 'contacts:write', 'contacts:read_all'
  ]) AS s
ON CONFLICT (slug) DO NOTHING;

DO $seed$
DECLARE
  v_model int;
BEGIN
  SELECT id INTO v_model FROM auth.tenants WHERE is_model ORDER BY id LIMIT 1;
  IF v_model IS NULL THEN
    RAISE NOTICE 'no model tenant -- role preset skipped';
    RETURN;
  END IF;
  IF EXISTS (SELECT 1 FROM auth.roles WHERE tenant_id = v_model) THEN
    RAISE NOTICE 'model tenant already has roles -- preset left alone';
    RETURN;
  END IF;

  INSERT INTO auth.roles (tenant_id, slug, name, type, portal, description) VALUES
    (v_model, 'owner', 'Firm owner', 'organisation', 'platform',
     'Full access to firm configuration, people, roles and every client in the firm.'),
    (v_model, 'adviser', 'Adviser', 'organisation', 'platform',
     'Advises clients. Sees only the assigned book (no firm-wide client visibility).'),
    (v_model, 'compliance', 'Compliance', 'organisation', 'platform',
     'Read-only oversight of every client, document, flow, case and task in the firm.'),
    (v_model, 'client', 'Client', 'organisation', 'client',
     'Client-portal access. Carries no permissions: RLS scopes a client to their own records.')
  ON CONFLICT (tenant_id, slug) DO NOTHING;

  INSERT INTO auth.role_permissions (role_id, permission_id)
  SELECT r.id, p.id
    FROM (VALUES
      -- owner: the firm principal.
      ('owner', 'members:read'), ('owner', 'members:write'),
      ('owner', 'roles:read'), ('owner', 'roles:write'),
      ('owner', 'branding:read'), ('owner', 'branding:write'),
      ('owner', 'contacts:read'), ('owner', 'contacts:write'), ('owner', 'contacts:read_all'),
      ('owner', 'documents:read'), ('owner', 'documents:write'),
      ('owner', 'forms:read'), ('owner', 'forms:write'),
      ('owner', 'templates:read'), ('owner', 'templates:write'),
      ('owner', 'datasets:read'), ('owner', 'datasets:write'),
      ('owner', 'flows:read'), ('owner', 'flows:write'),
      ('owner', 'cases:read'), ('owner', 'cases:write'),
      ('owner', 'tasks:read'), ('owner', 'tasks:write'),
      -- adviser: day-to-day client work, book-scoped (NO contacts:read_all).
      ('adviser', 'contacts:read'), ('adviser', 'contacts:write'),
      ('adviser', 'documents:read'), ('adviser', 'documents:write'),
      ('adviser', 'forms:read'),
      ('adviser', 'templates:read'),
      ('adviser', 'datasets:read'),
      ('adviser', 'flows:read'),
      ('adviser', 'cases:read'), ('adviser', 'cases:write'),
      ('adviser', 'tasks:read'), ('adviser', 'tasks:write'),
      -- compliance: firm-wide read, no writes.
      ('compliance', 'contacts:read'), ('compliance', 'contacts:read_all'),
      ('compliance', 'documents:read'),
      ('compliance', 'forms:read'),
      ('compliance', 'templates:read'),
      ('compliance', 'datasets:read'),
      ('compliance', 'flows:read'),
      ('compliance', 'cases:read'),
      ('compliance', 'tasks:read')
      -- client: none, by design.
    ) AS g(role_slug, perm_slug)
    JOIN auth.roles r ON r.tenant_id = v_model AND r.slug = g.role_slug
    JOIN auth.permissions p ON p.slug = g.perm_slug
  ON CONFLICT DO NOTHING;
END
$seed$;

RESET ROLE;
