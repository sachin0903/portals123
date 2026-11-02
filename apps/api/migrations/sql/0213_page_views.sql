-- Page views are navigations, not actions. audit.actions stays a record of mutations
-- and sign-ins; a GET is still not an action. This table is the separate stream the
-- profile heatmap reads. Path only: no query string, no host.

CREATE TABLE audit.page_views (
  id bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
  tenant_id int NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  actor_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  portal text NOT NULL CHECK (portal IN ('client', 'platform', 'operations')),
  path text NOT NULL CHECK (
    path ~ '^/'
    AND position('?' IN path) = 0
    AND position('#' IN path) = 0
    AND char_length(path) <= 512
  ),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX page_views_actor_day_idx
  ON audit.page_views (tenant_id, actor_id, created_at);

ALTER TABLE audit.page_views ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.page_views FORCE ROW LEVEL SECURITY;

-- A person records their own view. Firm staff can read the firm's views, which is
-- what a colleague's profile heatmap is. Operations reads run as service_role.
CREATE POLICY page_views_read ON audit.page_views
  FOR SELECT TO authenticated
  USING (tenant_id = auth.tenant_id());

CREATE POLICY page_views_insert ON audit.page_views
  FOR INSERT TO authenticated
  WITH CHECK (tenant_id = auth.tenant_id() AND actor_id = auth.uid());

GRANT SELECT, INSERT ON audit.page_views TO authenticated;
GRANT ALL ON audit.page_views TO service_role;
GRANT USAGE, SELECT ON SEQUENCE audit.page_views_id_seq TO authenticated, service_role;

COMMENT ON TABLE audit.page_views IS
  'Signed-in portal navigations (path only). Not an action event — see docs/api.md.';
