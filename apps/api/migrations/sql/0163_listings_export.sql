-- ============================================================================
-- 0163_listings_export.sql — grant backfill for listings:export (the 0042 idiom).
--
-- Dumping a listing to CSV / Excel / PDF is a different act from reading a page
-- of it. app/permissions.py gains listings:export as a named platform action,
-- the listing Export button hides without it, and the SvelteKit export routes
-- plus POST /exports/table.pdf refuse a missing grant. write does NOT imply
-- export (the implication only ever covers the matching :read).
--
-- sync_permissions() upserts the slug into auth.permissions on the next app
-- startup; what it cannot do is decide who HOLDS it, and without a backfill
-- every existing role that could export a listing yesterday loses the button
-- today. So this migration is pure grants — expand-only, idempotent (every
-- statement ON CONFLICT DO NOTHING), safe to re-run — granting the new slug to
-- every EXISTING role that holds contacts:read, contacts:write or
-- contacts:read_all. Those are the people who could already reach the book
-- listings (write implies read only at CHECK time, so a write-only role must
-- be named; read_all is the compliance shape that sees every client without
-- always also storing :read). NARROWING (taking export off a role that should
-- not keep it) is a deliberate act in the role editors afterwards, never
-- something a migration decides for a firm.
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
    ('listings:export', 'platform')
  ) AS s(slug, portal)
ON CONFLICT (slug) DO NOTHING;

-- listings:export ← contacts:read / contacts:write / contacts:read_all.
-- Anyone who could see a book listing yesterday could press Export on it.
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT DISTINCT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id
                              AND parent.slug IN ('contacts:read', 'contacts:write', 'contacts:read_all')
  JOIN auth.permissions child  ON child.slug = 'listings:export'
ON CONFLICT DO NOTHING;

-- The model tenant (the preset new firms clone). Owner, adviser and compliance
-- already hold a contacts read (0033), so the backfill above reaches them; this
-- is explicit so the preset says what it means even if a later editor takes
-- contacts:* off one of those roles and re-runs this file. The client-portal
-- role carries no permissions. Additive and idempotent: applied to whichever
-- of the preset roles exist, never creating roles (0033's non-destructive
-- contract).
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT r.id, p.id
  FROM (VALUES
    ('owner', 'listings:export'),
    ('adviser', 'listings:export'),
    ('compliance', 'listings:export')
  ) AS g(role_slug, perm_slug)
  JOIN auth.tenants t ON t.is_model
  JOIN auth.roles r ON r.tenant_id = t.id AND r.slug = g.role_slug
  JOIN auth.permissions p ON p.slug = g.perm_slug
ON CONFLICT DO NOTHING;

RESET ROLE;
