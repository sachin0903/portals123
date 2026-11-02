-- ============================================================================
-- 0093_portal_applications.sql -- store each portal's WorkOS APPLICATION client id.
--
-- Step P1 of the org-per-portal -> application-per-portal move (docs/auth-applications-migration.md,
-- A1: project-per-tenant preserved, one WorkOS application per portal INSIDE each firm's environment).
--
-- Today the portal is a WorkOS ORGANISATION (client_portal_org_id / platform_org_id /
-- operations_org_id) and auth.resolve_claims classifies the token's active org into the `portal`
-- claim. WorkOS applications each carry their OWN client id (confirmed against the Graphene env:
-- three distinct client_ids, one shared signing key -> JWKS verification is unchanged; the app is
-- identified by the token's client_id/aud claim). So the portal will be classified from the
-- application instead of the org.
--
-- This migration is EXPAND-ONLY and additive: it adds the per-application client-id columns ALONGSIDE
-- the existing *_org_id columns. Nothing reads them yet (that is P2/P3); no behaviour changes here.
-- The columns are nullable because they are populated per firm as its applications are created in the
-- WorkOS dashboard (tracked by the admin onboarding checklist, P6) -- an environment with none set
-- still resolves the portal from the org, exactly as today (dual-read during the transition).
-- ============================================================================

ALTER TABLE auth.tenant_environments
  ADD COLUMN IF NOT EXISTS client_portal_client_id text,   -- WorkOS Application client id for the Client Portal surface
  ADD COLUMN IF NOT EXISTS platform_client_id      text,   -- ... for the Platform (firm staff) surface
  ADD COLUMN IF NOT EXISTS operations_client_id    text;   -- ... for Internal Operations (Graphene core env only)

COMMENT ON COLUMN auth.tenant_environments.client_portal_client_id IS
  'WorkOS Application client id for the Client Portal surface (portal-via-application, 0093). The '
  'token''s client_id/aud is matched against this to classify the `portal` claim once P2/P3 land. '
  'NULL until the application is created in the WorkOS dashboard; a NULL falls back to org-based '
  'portal classification.';
COMMENT ON COLUMN auth.tenant_environments.platform_client_id IS
  'WorkOS Application client id for the Platform (firm staff) surface (portal-via-application, 0093).';
COMMENT ON COLUMN auth.tenant_environments.operations_client_id IS
  'WorkOS Application client id for the Internal Operations surface (Graphene core environment only; '
  'portal-via-application, 0093).';
