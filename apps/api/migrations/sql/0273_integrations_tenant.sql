-- ============================================================================
-- 0273_integrations_tenant -- repoints integrations.platforms.firm_id from SEI's own
-- firm code (integrations.firms, 0231_integrations_schema) to a real portals tenant
-- (auth.tenants.id), and drops the SEI DSR/Provider fields the admin edit drawer no longer offers.
--
-- WHY. The edit drawer's "Firm" field was always meant to say which portals tenant an
-- integration belongs to, but it was ported from the legacy system as an SEI firm CODE instead --
-- the documented gap between integrations.platforms and auth.tenants. This closes it: firm_id is
-- now a real FK, and the dropdown it feeds reads auth.tenants directly.
--
-- WHY THIS ALSO DROPS dsr_codes/provider_id/provider_name. dsr_codes only ever made sense paired
-- with firm_id-as-SEI-code: it resolved descriptions via integrations.dsrs, which is keyed by the
-- SAME SEI firm code firm_id no longer holds, so every description lookup would silently stop
-- resolving the moment this lands. provider_id/provider_name (0231_integrations_schema's own
-- "columns the admin screen ... needs") are logically independent of firm_id, but the admin edit
-- drawer is dropping the one dropdown that could ever set them, so the columns become
-- permanently and exclusively write-never-again; neither has ever held a non-null value, so there
-- is nothing to migrate forward. NOT touched: app_id/group_id (the SAME migration's "columns ...
-- the engine needs") -- sei_reports' own, unrelated to this screen.
--
-- Existing firm_id values are a different universe (SEI codes, not tenant ids) and cannot be
-- reinterpreted -- NULLed out via the type change's own USING clause, same as every row already
-- has today (0 of however-many rows have ever had one set). That retype runs ONLY when the column
-- is not already `int`: a USING expression forces a table rewrite even when the type is unchanged,
-- so running it again on a database that has this DDL (dev, under the uncommitted 0246 id) would
-- set every assigned firm_id to NULL. The tenant assignments made since are real data.
--
-- IF EXISTS/IF NOT EXISTS throughout, and the two ADD CONSTRAINTs wrapped in a duplicate_object
-- guard (Postgres has no native ADD CONSTRAINT IF NOT EXISTS, same idiom 0001_auth_core.sql uses
-- for CREATE ROLE): this id was applied to dev once already, uncommitted, under the id this
-- revision collided with and was renumbered away from (see this file's own migration module) --
-- so a database arriving here via the stub/merge already has every one of these objects, and this
-- must be a safe no-op for it, while a fresh database still gets them for the first time.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

DO $$ BEGIN
  IF (SELECT atttypid FROM pg_attribute
       WHERE attrelid = 'integrations.platforms'::regclass AND attname = 'firm_id') <> 'int4'::regtype THEN
    ALTER TABLE integrations.platforms DROP CONSTRAINT IF EXISTS platforms_firm_id_fkey;
    ALTER TABLE integrations.platforms DROP CONSTRAINT IF EXISTS platforms_firm_id_platform_name_tenant_id_key;
    ALTER TABLE integrations.platforms ALTER COLUMN firm_id TYPE int USING NULL;
  END IF;
END $$;

DO $$ BEGIN
  ALTER TABLE integrations.platforms
    ADD CONSTRAINT platforms_firm_id_fkey FOREIGN KEY (firm_id) REFERENCES auth.tenants(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  ALTER TABLE integrations.platforms
    ADD CONSTRAINT platforms_firm_id_platform_name_tenant_id_key UNIQUE (firm_id, platform_name, tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE integrations.platforms
  DROP COLUMN IF EXISTS dsr_codes,
  DROP COLUMN IF EXISTS provider_id,
  DROP COLUMN IF EXISTS provider_name;
