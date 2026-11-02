-- ============================================================================
-- 0198_contacts_sync_morgan_lloyd.sql — grant backfill for contacts:sync_morgan_lloyd
-- (First applied as 0180_contacts_sync_morgan_lloyd; that id remains a no-op stub.)
-- (the 0042 idiom).
--
-- Pushing a client and their SIPP to Morgan Lloyd creates records in a live CRM
-- that has no delete. app/permissions.py gains contacts:sync_morgan_lloyd as an
-- extra on the contacts resource, the account record's Sync to Morgan Lloyd
-- button hides without it, and POST /crm/.../sync-to-morgan-lloyd refuses a
-- missing grant. write does NOT imply it (the implication only ever covers the
-- matching :read).
--
-- sync_permissions() upserts the slug into auth.permissions on the next app
-- startup; what it cannot do is decide who HOLDS it, and without a backfill
-- every existing role that could press the button yesterday 403s today. So this
-- migration is pure grants — expand-only, idempotent (every statement ON
-- CONFLICT DO NOTHING), safe to re-run — granting the new slug to every
-- EXISTING role that holds contacts:write, so the re-gated endpoint keeps
-- working for exactly the people who could already reach it. NARROWING (taking
-- the sync off a role that should not keep it) is a deliberate act in the role
-- editors afterwards, never something a migration decides for a firm.
--
-- Propagation: auth.role_permissions is the mirror `environments.push_roles_to_workos`
-- publishes into each tenant's WorkOS project, so these grants reach access tokens
-- on the next role push per tenant — no WorkOS write happens here.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- Writes auth.permissions / auth.role_permissions, both FORCE RLS with no write
-- policy for the migrator; run elevated (the 0033/0042 idiom).
SET LOCAL ROLE service_role;

-- Migrate-time placeholder (`moon run api:migrate` runs BEFORE the app boots, so
-- on a fresh install this slug is not in auth.permissions yet and the grants below
-- would join to nothing). slug + derived resource/action + the correct portal only;
-- the first startup's sync_permissions() refreshes name/description. No slug is
-- invented here — it is declared in app/permissions.py.
INSERT INTO auth.permissions (slug, name, resource, action, portal, description)
SELECT s.slug, s.slug, split_part(s.slug, ':', 1), split_part(s.slug, ':', 2), s.portal,
       'Bootstrap placeholder; app/permissions.py is the source of truth and refreshes this row.'
  FROM (VALUES
    ('contacts:sync_morgan_lloyd', 'platform')
  ) AS s(slug, portal)
ON CONFLICT (slug) DO NOTHING;

-- contacts:sync_morgan_lloyd ← contacts:write. The button and both POST doors
-- gated write before the split, so its holders are exactly who could sync yesterday.
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id AND parent.slug = 'contacts:write'
  JOIN auth.permissions child  ON child.slug = 'contacts:sync_morgan_lloyd'
ON CONFLICT DO NOTHING;

-- The model tenant (the preset new firms clone). Owner and adviser already hold
-- contacts:write (0033), so the backfill above reaches them; this is explicit so
-- the preset says what it means even if a later editor takes write off one of
-- those roles and re-runs this file. Compliance is read-only and must not mint
-- a person in a live CRM. Additive and idempotent: applied to whichever of the
-- preset roles exist, never creating roles (0033's non-destructive contract).
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT r.id, p.id
  FROM (VALUES
    ('owner', 'contacts:sync_morgan_lloyd'),
    ('adviser', 'contacts:sync_morgan_lloyd')
  ) AS g(role_slug, perm_slug)
  JOIN auth.tenants t ON t.is_model
  JOIN auth.roles r ON r.tenant_id = t.id AND r.slug = g.role_slug
  JOIN auth.permissions p ON p.slug = g.perm_slug
ON CONFLICT DO NOTHING;

RESET ROLE;
