-- ============================================================================
-- 0127_permission_split.sql — grant backfill for the Team/Profiles/Types split
-- and the new firm_settings:show master switch (the 0042 idiom).
--
-- app/permissions.py gains seven permissions:
--
--   teams:read + teams:write         ← split out of members:*   (nav-layer only: the /members
--       endpoints still accept members:read/write too via require_any_permission, so this
--       backfill isn't load-bearing for the API — only for the sidebar, which gates Team on
--       teams:read alone, no OR)
--   profiles:read + profiles:write   ← split out of members:*   (same nav-layer split, for the
--       Profiles item)
--   document_types:read/write        ← split out of documents:admin (this one FULLY REPLACES
--       documents:admin at the enforcement layer — create/update-document-type now check
--       document_types:write, not documents:admin — so this backfill IS load-bearing)
--   firm_settings:show                new group-level gate wrapping the whole Firm settings nav
--       section, on top of each item's own permission — every role holding any one of that
--       group's existing gates needs it too, or the section vanishes for them entirely
--
-- sync_permissions() upserts the real name/description into auth.permissions on the next app
-- startup; what it cannot do is decide who HOLDS the new slugs, and without a backfill an
-- existing role that could reach Team/Profiles/Types/Firm-settings a moment ago loses them today.
-- Expand-only, idempotent (every statement ON CONFLICT DO NOTHING), safe to re-run.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- Writes auth.permissions / auth.role_permissions, both FORCE RLS with no write policy for the
-- migrator; run elevated (the 0033/0042 idiom).
SET LOCAL ROLE service_role;

-- Migrate-time placeholders (`moon run api:migrate` runs before the app boots, so these slugs
-- aren't in auth.permissions yet on a fresh deploy and the backfill joins below would hit
-- nothing). slug + derived resource/action + the correct portal only; the first startup's
-- sync_permissions() refreshes name/description from app/permissions.py, the source of truth.
INSERT INTO auth.permissions (slug, name, resource, action, portal, description)
SELECT s.slug, s.slug, split_part(s.slug, ':', 1), split_part(s.slug, ':', 2), s.portal,
       'Bootstrap placeholder; app/permissions.py is the source of truth and refreshes this row.'
  FROM (VALUES
    ('firm_settings:show',   'platform'),
    ('teams:read',           'platform'),
    ('teams:write',          'platform'),
    ('profiles:read',        'platform'),
    ('profiles:write',       'platform'),
    ('document_types:read',  'platform'),
    ('document_types:write', 'platform')
  ) AS s(slug, portal)
ON CONFLICT (slug) DO NOTHING;

-- ---- the backfill: each new slug to every existing holder of its parent ---------------------

-- document_types:write ← documents:admin. Its only effect was gating create/update-document-type,
-- now document_types:write's job — this backfill is what keeps those endpoints reachable.
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id AND parent.slug = 'documents:admin'
  JOIN auth.permissions child  ON child.slug = 'document_types:write'
ON CONFLICT DO NOTHING;

-- teams:read + profiles:read ← members:read. It covered both Team and Profiles indiscriminately
-- (one shared endpoint, filtered by a `kind` param), so every holder gets both new read slugs.
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id AND parent.slug = 'members:read'
  JOIN auth.permissions child  ON child.slug IN ('teams:read', 'profiles:read')
ON CONFLICT DO NOTHING;

-- teams:write + profiles:write ← members:write. Same reasoning, the write half.
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id AND parent.slug = 'members:write'
  JOIN auth.permissions child  ON child.slug IN ('teams:write', 'profiles:write')
ON CONFLICT DO NOTHING;

-- firm_settings:show ← any of the Firm settings group's existing gating permissions. This is the
-- one case where the "parent" is a SET of permissions rather than one slug: the group had no
-- single gate before, so anyone who could see ANY item in it needs the new group-level one.
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT DISTINCT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id
                              AND parent.slug IN (
    'members:read', 'members:write', 'teams:read', 'teams:write',
    'profiles:read', 'profiles:write', 'roles:read', 'roles:write',
    'branding:read', 'branding:write', 'forms:read', 'forms:write',
    'flows:read', 'flows:write', 'automations:read', 'automations:write',
    'templates:read', 'templates:write', 'email_templates:read', 'email_templates:write',
    'content:read', 'content:write', 'activity:read',
    'documents:admin', 'document_types:read', 'document_types:write',
    'datasets:read', 'datasets:write', 'data_sources:read'
  )
  JOIN auth.permissions child ON child.slug = 'firm_settings:show'
ON CONFLICT DO NOTHING;

RESET ROLE;
