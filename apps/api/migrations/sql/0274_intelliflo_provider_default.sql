-- ============================================================================
-- 0274_intelliflo_provider_default -- re-adds integrations.platforms.provider_id/provider_name,
-- dropped two migrations ago (0273_integrations_tenant), as DB-defaulted columns.
--
-- WHY THIS EXISTS. 0250 dropped them on the reasoning that the admin edit drawer no longer offers
-- a control that could ever set them, and neither had ever held a non-null value -- a fair read of
-- the DB and the admin UI alone. But services/integrations/intelliflo (contract.py, integration.py)
-- is still a real reader: every one of its five features sends `provider_id` as Intelliflo's own
-- `x-iflo-productProviderId` header on every API call, and IncomeStatements also sends
-- `provider_name` in its request body (endpoints/income_statements.py). 0250 broke all five,
-- for every tenant, in every environment -- a real regression, not a stale check.
--
-- WHY A DEFAULT RATHER THAN RESTORING THE OLD (nullable, admin-set) SHAPE. These were never
-- genuinely per-tenant data in the first place: one Intelliflo product-provider identity covers
-- our WHOLE integration, the same for every tenant synced (confirmed by
-- services/integrations/intelliflo's own local dev seed example, which hard-codes one
-- provider_id/provider_name pair for any tenant it seeds). The values below are that one real,
-- registered identity, not placeholders.
--
-- THIS IS A STOPGAP, not the final shape. The better home for a single deployment-wide constant is
-- provider-level settings (services/integrations/intelliflo/src/intelliflo_sync/providers/intelliflo/
-- config.py, alongside INTELLIFLO_APP_ID/INTELLIFLO_LIFE_CYCLE_ID), not a column on a per-tenant
-- row that nothing can ever set to a different value. Revisit once the rest of the datalake
-- migration is verified working end to end locally -- tracked, not forgotten.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE integrations.platforms
  ADD COLUMN IF NOT EXISTS provider_id text NOT NULL DEFAULT '5261',
  ADD COLUMN IF NOT EXISTS provider_name text NOT NULL DEFAULT 'Graphene';
