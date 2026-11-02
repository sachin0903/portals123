-- ============================================================================
-- 0096_firm_org.sql -- one firm org per environment (portal-via-application, A1).
--
-- Under portal-via-application the portal is the APP, not the org, so the three portal orgs
-- (client_portal_org_id / platform_org_id / operations_org_id) collapse to ONE org per firm: the
-- WorkOS org a firm's people are members of, which lets them authenticate. Which PORTALS a person may
-- use is our own `organisation_memberships.portal`, and the resolved `portal` claim comes from the
-- app (0094), so WorkOS no longer needs an org per portal.
--
-- Expand-only/additive: adds `firm_org_id` alongside the existing portal-org columns. Nothing reads
-- it until `ensure_firm_org` populates it and `portal_org_and_client` prefers it (COALESCE), so this
-- is behaviour-preserving. The portal-org columns are dropped later by P7. Operations stays on its own
-- org (Graphene ops in the core project) — this collapse is for a firm's client + platform surfaces.
-- ============================================================================

ALTER TABLE auth.tenant_environments ADD COLUMN IF NOT EXISTS firm_org_id text;

COMMENT ON COLUMN auth.tenant_environments.firm_org_id IS
  'The single WorkOS org a firm''s people belong to (one-firm-org, 0096/A1). Members authenticate '
  'via this org; which portals they may use is auth.organisation_memberships.portal, and the portal '
  'claim is app-derived (0094). Supersedes the per-portal *_org_id columns (dropped by P7).';
