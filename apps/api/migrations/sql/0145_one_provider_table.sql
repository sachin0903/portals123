-- ============================================================================
-- 0145_one_provider_table -- `platform.providers` folds into `platform.tenant_platforms`, and a
-- firm's provider connection is ONE ROW.
--
-- ---------------------------------------------------------------------------
-- WHAT THE TWO TABLES WERE.
-- ---------------------------------------------------------------------------
-- `platform.providers` (0029's `integrations.provider_sources`) was one row per named upstream
-- CONNECTION: `(provider, external_id, name, config)`, where `external_id` is a Microsoft tenant
-- GUID and `config` (0075) holds the vendor reference values that connection issues -- ML's
-- `firm_id`, `adviser_contact_id`, `scheme_type`, `sipp_type`.
--
-- `platform.tenant_platforms` is one row per FIRM's enablement: `(tenant_id, provider, config)`,
-- where `config` holds the slice filter -- `tenant_ids` for ML, `node_ids` for Seccl,
-- `firm_id`/`subfirm_ids`/`dsr_ids` for SEI.
--
-- ---------------------------------------------------------------------------
-- WHY ONE ROW IS RIGHT, AND WHY IT IS SPECIFICALLY MORGAN LLOYD'S SHAPE THAT SAYS SO.
-- ---------------------------------------------------------------------------
-- The two grains were only ever distinct in theory. `platform.providers` holds MORGAN LLOYD ROWS
-- AND NOTHING ELSE and that is deliberate (docs/provider-pipeline.md: "`platform.providers`
-- holding no Seccl or SEI rows is correct rather than a gap") -- and ML's connection is ONE
-- MICROSOFT TENANT PER FIRM. So the join was always 1:1 for the only provider that used it, and
-- `resolve_source_config` existed to walk it: read the firm's `tenant_ids`, refuse if there are
-- two, then look up the connection's config by the one that is left.
--
-- That refusal is the tell. A second connection for one firm has no meaning the code can act on
-- ("the wrong choice sends one firm's adviser-firm GUID with another firm's client, which the
-- vendor accepts as well-formed"), so the second grain was a shape nothing could use. Seccl and
-- SEI never had one: their connection is the deployment's credentials and their `config` is a
-- FILTER, which is why they carry no `platform.providers` row at all.
--
-- So: one `config`, on the row that names the firm. For ML it holds the Microsoft tenant id AND
-- that connection's reference values; for Seccl and SEI it holds the filter, as it already did.
-- `providers._CONFIG_KEYS` gains ML's four keys as OPTIONAL -- a half-configured connection is
-- still refused by `validate_config`, and the deployment settings remain the documented fallback
-- for the firm the vendor issues one pair for.
--
-- ---------------------------------------------------------------------------
-- WHAT THIS DELETES, AND IT IS THE POINT RATHER THAN A SIDE EFFECT.
-- ---------------------------------------------------------------------------
-- The two-step "register the Microsoft tenant, then enable a firm on it" becomes one step. There
-- is no registry to pick from, so `GET`/`PUT /admin/providers/{provider}/sources`,
-- `providers.list_sources`/`set_source`/`validate_source_config`, `platforms.provider_options`'s
-- ML branch and the console's "Register Morgan Lloyd Tenant" sheet all go: the tenant Platforms
-- card takes the Microsoft tenant id and the reference values directly.
--
-- That also deletes the route 0131's predecessor had to patch. `PUT …/providers/{provider}/sources`
-- is where the slug bug lived -- the one write that could not be given `morgan-lloyd` -- and it
-- was the prerequisite for enabling any firm on ML. A step that cannot be got wrong is better
-- than a step that has been fixed.
--
-- BACKFILL FIRST, DROP SECOND, in one migration: the target column already exists and the source
-- table is going, so there is no window in which both shapes are meaningful.
-- ============================================================================

-- SET LOCAL ROLE service_role, or the merge below is a no-op that reports success: the migrator
-- owns both tables and is not BYPASSRLS, and FORCE ROW LEVEL SECURITY binds the owner. 0059
-- documented this and it has been hit three times since.
SET LOCAL ROLE service_role;

-- The connection's reference values merge INTO the firm's config, and the firm's own keys win on
-- a collision. `||` is right-biased, so the enablement is the right-hand side: a firm that has
-- overridden a value locally must not have it overwritten by the registry it is being merged
-- from. `firm_id` is the one that can genuinely collide -- SEI's slice filter uses that name for
-- something else entirely -- and SEI has no `platform.providers` row, so the join finds nothing
-- for it either way.
UPDATE platform.tenant_platforms tp
   SET config = COALESCE(src.config, '{}'::jsonb) || tp.config
  FROM platform.providers src
 WHERE src.provider = tp.provider
   AND src.config <> '{}'::jsonb
   -- The connection this firm is enabled ON, matched through the slice key that names it. ML is
   -- the only provider with rows here and `tenant_ids` is its key; `?` asks whether the array
   -- contains the value, which is what makes this the firm's OWN connection rather than any.
   AND tp.config -> 'tenant_ids' ? src.external_id;

RESET ROLE;

DROP TABLE platform.providers;
