-- ============================================================================
-- 0102_operations_host.sql -- register the operations/admin portal's host, so it resolves to its
-- application by host like client/platform (env_config_by_host), rather than the $env fallback.
--
-- resolve_claims stays app-PREFERRED with the org fallback (0101): a session with a matching
-- application client id is classified from the app and gated by auth.organisation_memberships.portal;
-- one without (an internal/psql call, or a not-yet-app-configured firm) falls back to the org
-- classification. The fallback is dormant in production (every real session carries a client id) and
-- is the final "drop the per-portal orgs" cleanup, deferred deliberately.
-- ============================================================================

ALTER TABLE auth.tenant_environments ADD COLUMN IF NOT EXISTS operations_host text;
COMMENT ON COLUMN auth.tenant_environments.operations_host IS
  'Host the operations/admin portal is served on, so it resolves to its application by host like '
  'client/platform (one-firm-org). Only set on the Graphene tenant environment.';
