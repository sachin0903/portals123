-- Fold the `integrations` schema into `platform` and retire it.
--
-- 0029 created `integrations` as a connectivity-only schema, on the reasoning that provider
-- enablement was not domain data. In practice the split earned nothing: both tables are read
-- alongside `platform.accounts`/`platform.platforms` on every path that uses them, and having
-- two schemas that each answer "which platform" made the boundary a coin toss rather than a
-- rule. One schema, one answer.
--
--   integrations.tenant_providers  -> platform.tenant_platforms
--   integrations.provider_sources  -> platform.providers   (source_id -> external_id)
--
-- `external_id` because that is the name the same concept already carries everywhere else in
-- this schema (`platform_links.external_id`, the conformed provider models): the id the
-- UPSTREAM knows the thing by. `source_id` was a third word for it.
--
-- Pure rename. No column added, dropped or retyped, so it is reversible and nothing needs
-- backfilling. ALTER TABLE ... SET SCHEMA carries indexes, constraints, triggers, policies,
-- grants and the identity sequence with the table.

ALTER TABLE integrations.tenant_providers SET SCHEMA platform;
ALTER TABLE platform.tenant_providers RENAME TO tenant_platforms;

ALTER TABLE integrations.provider_sources SET SCHEMA platform;
ALTER TABLE platform.provider_sources RENAME TO providers;
ALTER TABLE platform.providers RENAME COLUMN source_id TO external_id;

-- Constraints, indexes and policies keep their old names through a rename, which leaves `\d`
-- describing a table that no longer exists. Rename them too rather than leaving the next
-- person to work out that `tenant_providers_tenant_provider_key` belongs to `tenant_platforms`.
ALTER TABLE platform.tenant_platforms
  RENAME CONSTRAINT tenant_providers_tenant_provider_key TO tenant_platforms_tenant_provider_key;
ALTER INDEX platform.tenant_providers_tenant_id_idx RENAME TO tenant_platforms_tenant_id_idx;
ALTER POLICY tenant_providers_read ON platform.tenant_platforms RENAME TO tenant_platforms_read;

ALTER TABLE platform.providers
  RENAME CONSTRAINT provider_sources_provider_source_key TO providers_provider_external_key;

COMMENT ON TABLE platform.tenant_platforms IS
  'Which platforms a tenant has enabled, and the filter selecting THEIR slice of the whole-firm '
  'core data (the fan-out predicate). See app/fanout.py and docs/provider-routing.md.';
COMMENT ON TABLE platform.providers IS
  'Known upstream sources per platform, with a display name -- e.g. each Morgan Lloyd Microsoft '
  'tenant (one Dynamics/Azure tenant per firm). Drives the admin enablement dropdown. '
  'Operations-facing reference data: service_role only.';
COMMENT ON COLUMN platform.providers.external_id IS
  'The id the UPSTREAM knows this source by (a Microsoft tenant GUID, a Seccl node, an SEI firm '
  'id). Named to match platform_links.external_id, which is the same concept.';

-- The sequence grant in 0029 was written against the schema, not the sequence, so it did not
-- follow the table. Re-grant explicitly.
GRANT USAGE ON ALL SEQUENCES IN SCHEMA platform TO service_role;

-- Nothing else was ever created here -- 0029's header reserved the schema for
-- providers/connections/sync_jobs that never arrived. RESTRICT rather than CASCADE: if
-- something IS still in there, this migration should fail loudly rather than delete it.
DROP SCHEMA integrations RESTRICT;
