-- ============================================================================
-- 0042_permission_splits.sql — grant backfill for the six approved permission splits.
--
-- app/permissions.py (the registry, single source of truth) gains six finer-grained
-- permissions, and the endpoints they gate stop accepting the coarser parent:
--
--   instructions:read + instructions:write  ← split out of contacts:write   (money movement:
--       the instructions list/create/cancel, the bank-account + account-model PUTs, drawdown)
--   documents:send                           ← split out of documents:write  (client-facing
--       release: send, request create, request-item reassign)
--   illustrations:write                      ← split out of documents:write  (both generators)
--   forms:submit                             ← split out of forms:write      (the response
--       plane: submitting + reading responses; response LISTING also leaves forms:read)
--   pages:write                              ← split out of branding:write   (portal
--       composition: a change to Branding.pages on the branding PUTs)
--   platforms:query                          ← split out of platforms:read   (the lake SQL
--       console, operations portal)
--
-- sync_permissions() upserts the new slugs into auth.permissions on the next app startup; what
-- it cannot do is decide who HOLDS them, and without a backfill every existing role that could
-- act yesterday 403s today. So this migration is pure grants — expand-only, idempotent (every
-- statement ON CONFLICT DO NOTHING), safe to re-run — granting each new slug to every EXISTING
-- role that holds the parent it was split from, so the re-gated endpoints keep working for
-- exactly the people who could already reach them. NARROWING (taking a new slug off a role that
-- should not keep it) is a deliberate act in the role editors afterwards, never something a
-- migration decides for a firm.
--
-- Propagation: auth.role_permissions is the mirror `environments.push_roles_to_workos` publishes
-- into each tenant's WorkOS project, so these grants reach access tokens on the next role push
-- per tenant — no WorkOS write happens here.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- Writes auth.permissions / auth.role_permissions, both FORCE RLS with no write policy for the
-- migrator; run elevated (the 0033 idiom).
SET LOCAL ROLE service_role;

-- Migrate-time placeholders for the new permission rows (the 0033 header note): `moon run
-- api:migrate` runs BEFORE the app boots, so on a fresh install these slugs are not in
-- auth.permissions yet and the grants below would join to nothing. slug + derived
-- resource/action + the correct portal only; the first startup's sync_permissions() refreshes
-- name/description. No slug is invented here — every one is declared in app/permissions.py.
INSERT INTO auth.permissions (slug, name, resource, action, portal, description)
SELECT s.slug, s.slug, split_part(s.slug, ':', 1), split_part(s.slug, ':', 2), s.portal,
       'Bootstrap placeholder; app/permissions.py is the source of truth and refreshes this row.'
  FROM (VALUES
    ('instructions:read',   'platform'),
    ('instructions:write',  'platform'),
    ('documents:send',      'platform'),
    ('illustrations:write', 'platform'),
    ('forms:submit',        'platform'),
    ('pages:write',         'platform'),
    -- operations portal: the portal column matters even as a placeholder, because the admin
    -- role editor groups by it (rbac.OPERATIONS_ONLY itself derives from the Python registry,
    -- not this column, so no privilege rides on the value).
    ('platforms:query',     'operations')
  ) AS s(slug, portal)
ON CONFLICT (slug) DO NOTHING;

-- ---- the backfill: each new slug to every existing holder of its parent ---------------------
-- One INSERT per split. The child JOIN carries no relation to rp on purpose: each parent-holding
-- role gets every listed child (a cross join over the child set).

-- instructions:* ← contacts:write. The money-movement endpoints all gated contacts:write before
-- the split (including the instructions LIST — the read-behind-write this split fixes), so its
-- holders are exactly who could raise/cancel/see instructions yesterday. Both read and write:
-- write implies read only at CHECK time, and the mirror should say what a role really holds.
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id AND parent.slug = 'contacts:write'
  JOIN auth.permissions child  ON child.slug IN ('instructions:read', 'instructions:write')
ON CONFLICT DO NOTHING;

-- documents:send + illustrations:write ← documents:write. Send/request/reassign and both
-- illustration generators gated documents:write before the split.
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id AND parent.slug = 'documents:write'
  JOIN auth.permissions child  ON child.slug IN ('documents:send', 'illustrations:write')
ON CONFLICT DO NOTHING;

-- forms:submit ← forms:write ONLY (deliberately NOT forms:read): response submission gated
-- forms:write before the split, so write holders keep it. forms:read holders LOSE response
-- LISTING by design — that is the split's point (form-definition viewers stop reading client
-- submissions); the listing now accepts forms:submit OR documents:read, and read-only oversight
-- roles typically hold documents:read already.
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id AND parent.slug = 'forms:write'
  JOIN auth.permissions child  ON child.slug = 'forms:submit'
ON CONFLICT DO NOTHING;

-- pages:write ← branding:write. Anyone who could save branding could recompose the client
-- portal's pages before the split (pages ride the branding object).
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id AND parent.slug = 'branding:write'
  JOIN auth.permissions child  ON child.slug = 'pages:write'
ON CONFLICT DO NOTHING;

-- platforms:query ← platforms:read AND platforms:write holders. The console gated
-- platforms:read before the split, and a role holding only platforms:write satisfied that gate
-- through the write⇒read implication — the implication does not cross to a sibling action, so
-- both parents backfill or a write-only operations role would silently lose the console.
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT DISTINCT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id
                              AND parent.slug IN ('platforms:read', 'platforms:write')
  JOIN auth.permissions child  ON child.slug = 'platforms:query'
ON CONFLICT DO NOTHING;

-- ---- the model tenant (the preset new firms clone) ------------------------------------------
-- The survey's mapping, seeded explicitly so the preset says what it means even where the
-- backfill above would not reach it (the model adviser holds only forms:read, so forms:submit
-- must be explicit; compliance never held contacts:write, so its NEW instructions:read — the
-- read-behind-write fix — is a capability it gains here, not a grandfathered one). Additive and
-- idempotent: applied to whichever of the preset roles exist, never creating roles, so an
-- install whose model was customised in the admin /model UI keeps its shape (0033's
-- non-destructive contract) and merely gains the split slugs its roles imply.
INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT r.id, p.id
  FROM (VALUES
    -- owner: the firm principal — everything the firm self-serves, including composition.
    ('owner', 'instructions:read'), ('owner', 'instructions:write'),
    ('owner', 'documents:send'),
    ('owner', 'illustrations:write'),
    ('owner', 'forms:submit'),
    ('owner', 'pages:write'),
    -- adviser: day-to-day client work — moves money, releases documents, runs illustrations,
    -- fills forms; deliberately NOT pages:write (composing the portal is firm configuration).
    ('adviser', 'instructions:read'), ('adviser', 'instructions:write'),
    ('adviser', 'documents:send'),
    ('adviser', 'illustrations:write'),
    ('adviser', 'forms:submit'),
    -- compliance: firm-wide READ oversight — may now SEE the pending money-movement queue
    -- (the point of instructions:read existing) and still writes nothing anywhere.
    ('compliance', 'instructions:read')
    -- client: none, by design (client-portal roles carry no permissions).
  ) AS g(role_slug, perm_slug)
  JOIN auth.tenants t ON t.is_model
  JOIN auth.roles r ON r.tenant_id = t.id AND r.slug = g.role_slug
  JOIN auth.permissions p ON p.slug = g.perm_slug
ON CONFLICT DO NOTHING;

RESET ROLE;
