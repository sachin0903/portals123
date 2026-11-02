-- ============================================================================
-- 0115_catalogue_uids.sql -- the last thirteen tables a URL names get an address.
--
-- Every remaining serial in an API path belonged to a table with no `uid` to use instead:
--
--   platform.models                 a firm's investment model
--   platform.products               a shelf item
--   platform.model_versions         one reviewable version of a model
--   platform.model_commentary       a note against a model
--   platform.fee_packages           a charge structure
--   platform.platforms              a custodian in the registry
--   platform.third_party_providers  a third-party product provider
--   platform.transfer_providers     a ceding scheme
--   auth.tenant_environments        a firm's WorkOS routing row
--   storage.user_credentials        a staff member's object-store key
--   documents.document_types        a firm's own document type
--   flows.outbox                    a dead-lettered outbox entry an operator replays
--   documents.render_jobs           a dead-lettered render job an operator replays
--
-- The pattern is `0114_api_log_uid`'s: NULLable, then defaulted, then backfilled, then
-- constrained. `ADD COLUMN ... NOT NULL DEFAULT nanoid(12)` in one step would REWRITE each table
-- under an ACCESS EXCLUSIVE lock, because Postgres' fast path for a defaulted column requires a
-- non-volatile default and nanoid is volatile by necessity.
--
-- THE BACKFILL RUNS AS service_role, and that is not decoration. Several of these tables have
-- FORCE ROW LEVEL SECURITY and the migrator role is not BYPASSRLS, so the UPDATE sees no rows,
-- fills nothing, and the SET NOT NULL below fails with "column uid contains null values" -- on a
-- database that has rows, which a fresh one does not. That is exactly how this migration passed
-- against an empty scratch database and then failed 1,468 tests: the seeded `documents.document_types`
-- was the first table it met that already held any. The trap 0042's seed and 0059's backfill
-- both record.
--
-- The DDL stays as the MIGRATOR, which owns these tables -- `service_role` cannot ALTER them.
--
-- `flows.outbox` and `documents.render_jobs` are the two worth pausing on: they are QUEUES, and an
-- indexed text column is maintained on every insert. They are here because a URL names one -- the
-- dead-letter replay (`/flows/dead-letters/{kind}/{row_uid}/replay`) -- and a row an operator can
-- address is a row with an address. The cost is one 12-character column and one index on tables
-- whose depth is measured in the low hundreds; if either became a hot queue, the honest answer
-- would be to reconsider the endpoint rather than the column.
--
-- NOT here: `platform.transactions` and `platform.product_platforms`. Nothing addresses either --
-- a movement is not something a URL names, and a product's platform reference is reached through
-- the product. A column added on the chance that something might is a column nobody maintains.
--
-- Not CONCURRENTLY, per the convention 0024 records: these run inside Alembic's transaction, which
-- CONCURRENTLY cannot.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- ---- 1. The column and its default (as the migrator, which owns these) ------
ALTER TABLE platform.models                ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE platform.models                ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.products              ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE platform.products              ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.model_versions        ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE platform.model_versions        ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.model_commentary      ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE platform.model_commentary      ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.fee_packages          ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE platform.fee_packages          ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.platforms             ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE platform.platforms             ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.third_party_providers ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE platform.third_party_providers ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE platform.transfer_providers    ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE platform.transfer_providers    ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE auth.tenant_environments       ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE auth.tenant_environments       ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE storage.user_credentials       ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE storage.user_credentials       ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.document_types       ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE documents.document_types       ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE flows.outbox                   ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE flows.outbox                   ALTER COLUMN uid SET DEFAULT public.nanoid(12);
ALTER TABLE documents.render_jobs          ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE documents.render_jobs          ALTER COLUMN uid SET DEFAULT public.nanoid(12);

-- ---- 2. The backfill (as service_role, to see through FORCE RLS) -----------
SET LOCAL ROLE service_role;

UPDATE platform.models                SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE platform.products              SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE platform.model_versions        SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE platform.model_commentary      SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE platform.fee_packages          SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE platform.platforms             SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE platform.third_party_providers SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE platform.transfer_providers    SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE auth.tenant_environments       SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE storage.user_credentials       SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE documents.document_types       SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE flows.outbox                   SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE documents.render_jobs          SET uid = public.nanoid(12) WHERE uid IS NULL;

RESET ROLE;

-- ---- 3. The constraint and the address index -------------------------------
ALTER TABLE platform.models                ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS platform_models_uid_key ON platform.models(uid);
ALTER TABLE platform.products              ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS platform_products_uid_key ON platform.products(uid);
ALTER TABLE platform.model_versions        ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS platform_model_versions_uid_key ON platform.model_versions(uid);
ALTER TABLE platform.model_commentary      ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS platform_model_commentary_uid_key ON platform.model_commentary(uid);
ALTER TABLE platform.fee_packages          ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS platform_fee_packages_uid_key ON platform.fee_packages(uid);
ALTER TABLE platform.platforms             ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS platform_platforms_uid_key ON platform.platforms(uid);
ALTER TABLE platform.third_party_providers ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS platform_third_party_providers_uid_key ON platform.third_party_providers(uid);
ALTER TABLE platform.transfer_providers    ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS platform_transfer_providers_uid_key ON platform.transfer_providers(uid);
ALTER TABLE auth.tenant_environments       ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS auth_tenant_environments_uid_key ON auth.tenant_environments(uid);
ALTER TABLE storage.user_credentials       ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS storage_user_credentials_uid_key ON storage.user_credentials(uid);
ALTER TABLE documents.document_types       ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS documents_document_types_uid_key ON documents.document_types(uid);
ALTER TABLE flows.outbox                   ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS flows_outbox_uid_key ON flows.outbox(uid);
ALTER TABLE documents.render_jobs          ALTER COLUMN uid SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS documents_render_jobs_uid_key ON documents.render_jobs(uid);
