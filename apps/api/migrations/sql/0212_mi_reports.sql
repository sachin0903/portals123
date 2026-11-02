-- Which shipped management reports a firm may run.
--
-- The query itself is not stored here. It lives in app/reports/catalogue.py, reviewed like the
-- document dataset catalogue. This row is only the switch: off until operations turns it on,
-- and on for every firm at once. A later per-firm override can be a second table; this one
-- stays the Graphene-wide default.

CREATE TABLE platform.mi_reports (
    code       text        PRIMARY KEY,
    enabled    boolean     NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by uuid        REFERENCES auth.users(id) ON DELETE SET NULL,
    CONSTRAINT mi_reports_code_shape CHECK (code ~ '^[a-z][a-z0-9_]{0,63}$')
);

ALTER TABLE platform.mi_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.mi_reports FORCE ROW LEVEL SECURITY;

-- Staff may see which reports are on. There is no write policy: a firm session cannot
-- change the switch. Operations updates run as service_role, which bypasses RLS.
CREATE POLICY mi_reports_read ON platform.mi_reports
    FOR SELECT TO authenticated
    USING (true);

GRANT SELECT ON platform.mi_reports TO authenticated;
GRANT ALL ON platform.mi_reports TO service_role;

-- Grants. sync_permissions() inserts the slugs on the next boot, which is too late for
-- this file and does not decide who holds them. Placeholders here, refreshed on startup.
-- reports:* goes to whoever can already see the book (the listings:export population):
-- an adviser exports the clients RLS already allows, and export is a separate grant a
-- firm can take off afterwards. mi_reports:* follows platform terms, the screen beside this one.
SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE service_role;

INSERT INTO auth.permissions (slug, name, resource, action, portal, description)
SELECT s.slug, s.slug, split_part(s.slug, ':', 1), split_part(s.slug, ':', 2), s.portal,
       'Bootstrap placeholder; app/permissions.py is the source of truth and refreshes this row.'
  FROM (VALUES
    ('reports:read', 'platform'),
    ('reports:export', 'platform'),
    ('mi_reports:read', 'operations'),
    ('mi_reports:write', 'operations')
  ) AS s(slug, portal)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT DISTINCT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id
                              AND parent.slug IN ('contacts:read', 'contacts:write', 'contacts:read_all')
  JOIN auth.permissions child  ON child.slug IN ('reports:read', 'reports:export')
ON CONFLICT DO NOTHING;

INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT r.id, p.id
  FROM (VALUES
    ('owner', 'reports:read'),
    ('owner', 'reports:export'),
    ('adviser', 'reports:read'),
    ('adviser', 'reports:export'),
    ('compliance', 'reports:read'),
    ('compliance', 'reports:export')
  ) AS g(role_slug, perm_slug)
  JOIN auth.tenants t ON t.is_model
  JOIN auth.roles r ON r.tenant_id = t.id AND r.slug = g.role_slug
  JOIN auth.permissions p ON p.slug = g.perm_slug
ON CONFLICT DO NOTHING;

INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT DISTINCT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id
                              AND parent.slug IN ('platform_terms:read', 'platform_terms:write')
  JOIN auth.permissions child  ON child.slug = 'mi_reports:read'
ON CONFLICT DO NOTHING;

INSERT INTO auth.role_permissions (role_id, permission_id)
SELECT DISTINCT rp.role_id, child.id
  FROM auth.role_permissions rp
  JOIN auth.permissions parent ON parent.id = rp.permission_id
                              AND parent.slug = 'platform_terms:write'
  JOIN auth.permissions child  ON child.slug = 'mi_reports:write'
ON CONFLICT DO NOTHING;

RESET ROLE;
