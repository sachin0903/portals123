-- ============================================================================
-- 0143_one_provider_vocabulary -- the provider is spelled ONCE, in an enum, and
-- `platform.platforms` is gone.
--
-- ---------------------------------------------------------------------------
-- WHY THE TABLE GOES.
-- ---------------------------------------------------------------------------
-- `platform.platforms` (0002) held one row per provider and existed to be a FOREIGN KEY TARGET for
-- eight columns. It carried nothing else: `name` is the provider's display name, which
-- `PROVIDER_META` already owns and which `sync_platforms` deliberately does not overwrite;
-- `config` was dropped unread by 0130; `provider` is the registry key. So the table was a
-- surrogate id for a value that is already a closed vocabulary in code — and paying for it with a
-- join meant `platform.products.platform_id` could be NULL, which is what emptied
-- `GET /catalog/products` for a whole book, and meant `merge.py` had to resolve
-- `platforms.provider -> id` on every run, rejecting every product for every provider when
-- nothing had seeded the registry (0 of 373 accounts carried a product for months).
--
-- The vocabulary lives where the other closed vocabularies live: `app/providers.py::PROVIDERS`,
-- the way `permissions.sync_permissions` and `PRODUCT_TYPES` do. The DATABASE gets an enum, which
-- is what `platform.product_type` already is for the same kind of fact.
--
-- ---------------------------------------------------------------------------
-- WHY `morgan-lloyd` AND NOT `morgan_lloyd`.
-- ---------------------------------------------------------------------------
-- The provider had THREE spellings: `morgan_lloyd` (the registry key, the DB value, every Python
-- dict key), `morgan-lloyd` (the URL slug, the service directory, the moon id, the docker service)
-- and `ml` (the warehouse schema). Morgan Lloyd was the only provider where any two differed, and
-- it was consequently the only provider that could break on a missing conversion — which it did:
-- `PUT /admin/providers/{provider}/sources` compared a slug against the keys and refused to
-- register the Microsoft tenant that enabling any firm depends on.
--
-- One spelling, and the hyphen wins because it is the one a person types and the one the
-- filesystem already uses. `PROVIDER_META['slug']`, `by_slug()` and `_known_provider()` are
-- deleted with it: there is no conversion left to forget. The warehouse schema stays `ml` for now
-- and 0133 renames it, at which point `provider_schema()` becomes `code.replace('-','_')` and its
-- last hardcoded map goes too.
--
-- Every existing row is `seccl` except ONE `tenant_platforms` row, so the data migration is a
-- single value in practice. It is written as a CASE anyway, because staging and production are
-- not this database.
--
-- ---------------------------------------------------------------------------
-- WHICH COLUMNS THE ENUM APPLIES TO.
-- ---------------------------------------------------------------------------
-- Every column naming a custodian we integrate with as a CONFIGURATION or MAPPING fact, including
-- the ledgers — `platform.merge_runs`/`merge_rejects` are our own writes from the same registry,
-- and leaving them text would put the two spellings straight back.
--
-- **A member is therefore never REMOVED from this enum.** A ledger row naming a provider we have
-- stopped integrating with must keep reading as words for ever (the rule `audit.actions` sets), and
-- an enum member costs nothing to keep.
--
-- TWO EXCEPTIONS, both deliberate:
--   * `audit.api_log.provider` stays TEXT. It records an ATTEMPT, including calls with no tenant
--     and operations we do not recognise; it is an observation of what happened rather than a
--     statement of what is configured, which is the same reason `/platforms/api/[id]` is the one
--     documented exception to the no-serials-in-URLs rule.
--   * `crm.kyc_checks.provider` is a DIFFERENT VOCABULARY — Creditsafe, and iDenfy before it. It
--     names a screening provider, not a custodian, and constraining it to this enum would be a
--     category error that happens to compile.
-- ============================================================================

-- A BOUNDED WAIT. This is the heaviest-locking migration on the branch — `ALTER TABLE
-- platform.accounts ADD COLUMN` / `DROP COLUMN`, seven backfill UPDATEs, `DROP TABLE
-- platform.platforms` and a `CREATE INDEX` — and without a timeout an ACCESS EXCLUSIVE wait behind
-- one open reader stalls the deploy with all of that still to come. 0066, 0094, 0111, 0114 and 0115
-- all set the same 5s for comparable work on the same tables.
--
-- NOT `CONCURRENTLY` for the index: 0047, 0049 and 0114 each record that it is illegal inside
-- Alembic's transaction, and that `lock_timeout` is the substitute.
SET LOCAL lock_timeout = '5s';

CREATE TYPE platform.provider_code AS ENUM ('sei', 'seccl', 'morgan-lloyd');

COMMENT ON TYPE platform.provider_code IS
  'The custodians and scheme administrators this platform integrates with. Mirrors '
  '`app/providers.py::PROVIDERS`, which is the source of truth for what exists; this is what the '
  'database will admit. Members are ADDED and never removed -- ledger rows in '
  'platform.merge_runs must keep reading as words after we stop integrating with someone.';

-- The view names both `links.provider` and the two `source_system` columns in its body, so it has
-- to go first and come back after (0043 records the same dance, and the reason for
-- `security_invoker`: without it the view runs as its OWNER, the migrator, which is subject to
-- FORCE ROW LEVEL SECURITY and carries no claims -- so every policy evaluates against an empty
-- lineage and the view returns zero rows to everyone).
DROP VIEW IF EXISTS platform.entity_links;

-- The CHECKs go with the type: the enum IS the constraint now, and a text CHECK naming the old
-- spelling would refuse every row the new vocabulary writes.
ALTER TABLE platform.tenant_platforms DROP CONSTRAINT IF EXISTS tenant_providers_provider_check;
ALTER TABLE platform.providers DROP CONSTRAINT IF EXISTS provider_sources_provider_check;
ALTER TABLE platform.transaction_expectations
  DROP CONSTRAINT IF EXISTS transaction_expectations_provider_check;

-- USING with a CASE, not a bare cast: `morgan_lloyd` is not a member of the enum, so a plain
-- `::platform.provider_code` would fail on exactly the row this migration exists to move.
ALTER TABLE platform.tenant_platforms
  ALTER COLUMN provider TYPE platform.provider_code
  USING (CASE provider WHEN 'morgan_lloyd' THEN 'morgan-lloyd' ELSE provider END)
        ::platform.provider_code;

ALTER TABLE platform.providers
  ALTER COLUMN provider TYPE platform.provider_code
  USING (CASE provider WHEN 'morgan_lloyd' THEN 'morgan-lloyd' ELSE provider END)
        ::platform.provider_code;

ALTER TABLE platform.links
  ALTER COLUMN provider TYPE platform.provider_code
  USING (CASE provider WHEN 'morgan_lloyd' THEN 'morgan-lloyd' WHEN 'ml' THEN 'morgan-lloyd'
                       ELSE provider END)::platform.provider_code;

ALTER TABLE platform.orgs
  ALTER COLUMN provider TYPE platform.provider_code
  USING (CASE provider WHEN 'morgan_lloyd' THEN 'morgan-lloyd' ELSE provider END)
        ::platform.provider_code;

ALTER TABLE platform.portfolios
  ALTER COLUMN provider TYPE platform.provider_code
  USING (CASE provider WHEN 'morgan_lloyd' THEN 'morgan-lloyd' ELSE provider END)
        ::platform.provider_code;

ALTER TABLE platform.transaction_expectations
  ALTER COLUMN provider TYPE platform.provider_code
  USING (CASE provider WHEN 'morgan_lloyd' THEN 'morgan-lloyd' ELSE provider END)
        ::platform.provider_code;

ALTER TABLE platform.merge_runs
  ALTER COLUMN provider TYPE platform.provider_code
  USING (CASE provider WHEN 'morgan_lloyd' THEN 'morgan-lloyd' ELSE provider END)
        ::platform.provider_code;

ALTER TABLE platform.merge_rejects
  ALTER COLUMN provider TYPE platform.provider_code
  USING (CASE provider WHEN 'morgan_lloyd' THEN 'morgan-lloyd' ELSE provider END)
        ::platform.provider_code;

-- `source_system` is the same fact under a different name — the provider a row came from. It is
-- part of `platform.valuations`' natural key `(account_id, as_of_date, source_system)`, which is
-- what lets one pension account hold a custodian's valuation AND the scheme administrator's for
-- the same day (0117); leaving it TEXT would reintroduce the two spellings on the one column where
-- a mismatch silently creates a second row rather than updating the first.
ALTER TABLE platform.transactions
  ALTER COLUMN source_system TYPE platform.provider_code
  USING (CASE source_system WHEN 'morgan_lloyd' THEN 'morgan-lloyd' ELSE source_system END)
        ::platform.provider_code;

ALTER TABLE platform.valuations
  ALTER COLUMN source_system TYPE platform.provider_code
  USING (CASE source_system WHEN 'morgan_lloyd' THEN 'morgan-lloyd' ELSE source_system END)
        ::platform.provider_code;

-- ---------------------------------------------------------------------------
-- THE EIGHT FOREIGN KEYS BECOME THE VALUE THEY WERE POINTING AT.
-- ---------------------------------------------------------------------------
-- Expand, backfill, contract -- in one migration because the target table is being dropped, so
-- there is no window in which both shapes are meaningful.

-- NOT `platform.products`. A shelf item is ONE thing however many custodians hold it — which is
-- why 0044 moved the reference to the child and 0130 dropped `products.platform_id` — so the
-- product carries no provider and its mapping row carries one each.
ALTER TABLE platform.product_platforms ADD COLUMN IF NOT EXISTS provider platform.provider_code;
ALTER TABLE platform.models            ADD COLUMN IF NOT EXISTS provider platform.provider_code;
ALTER TABLE platform.model_platforms   ADD COLUMN IF NOT EXISTS provider platform.provider_code;
ALTER TABLE platform.fee_packages      ADD COLUMN IF NOT EXISTS provider platform.provider_code;
ALTER TABLE platform.fee_package_platforms
  ADD COLUMN IF NOT EXISTS provider platform.provider_code;
ALTER TABLE platform.accounts          ADD COLUMN IF NOT EXISTS custodian platform.provider_code;
ALTER TABLE crm.applications           ADD COLUMN IF NOT EXISTS provider platform.provider_code;

-- SET LOCAL ROLE service_role: every statement below is DML over a FORCE-RLS table, and the
-- migrator is not BYPASSRLS. Without it each UPDATE matches the rows its absent
-- `request.jwt.claims` allows -- none -- and reports success, which is how 0127 came to fail a
-- SET NOT NULL against rows the same transaction had just been told did not exist.
SET LOCAL ROLE service_role;

-- NORMALISE THE REGISTRY FIRST, or the seven casts below break the rule this file states 80 lines
-- above. `platform.platforms.provider` is TEXT and holds `morgan_lloyd`: it is written by main's
-- `providers.sync_platforms()` on every API startup from a `PROVIDERS` tuple spelled with an
-- underscore, so every deployed database has that row. A bare `::platform.provider_code` on it
-- raises `invalid input value for enum platform.provider_code: "morgan_lloyd"` and aborts the
-- upgrade mid-deploy, on exactly the provider this migration exists to re-spell.
--
-- Stated ONCE here rather than as a CASE repeated seven times, because seven copies of a
-- normalisation is seven places for the eighth backfill to forget it. The table is dropped at the
-- end of this migration, so this value lives only for the length of the transaction.
--
-- The suite cannot catch this: it builds every database from scratch, where `platform.platforms`
-- is empty, the joins below match no rows and the cast is never evaluated.
UPDATE platform.platforms SET provider = 'morgan-lloyd' WHERE provider = 'morgan_lloyd';

UPDATE platform.product_platforms t SET provider = p.provider::text::platform.provider_code
  FROM platform.platforms p WHERE p.id = t.platform_id;
UPDATE platform.models t SET provider = p.provider::text::platform.provider_code
  FROM platform.platforms p WHERE p.id = t.platform_id;
UPDATE platform.model_platforms t SET provider = p.provider::text::platform.provider_code
  FROM platform.platforms p WHERE p.id = t.platform_id;
UPDATE platform.fee_packages t SET provider = p.provider::text::platform.provider_code
  FROM platform.platforms p WHERE p.id = t.platform_id;
UPDATE platform.fee_package_platforms t SET provider = p.provider::text::platform.provider_code
  FROM platform.platforms p WHERE p.id = t.platform_id;
UPDATE platform.accounts t SET custodian = p.provider::text::platform.provider_code
  FROM platform.platforms p WHERE p.id = t.primary_custodian;
UPDATE crm.applications t SET provider = p.provider::text::platform.provider_code
  FROM platform.platforms p WHERE p.id = t.platform_id;

RESET ROLE;

-- The unique keys and indexes that named the surrogate move to the value. `product_platforms` is
-- keyed `(product_id, platform_id)` and that grain is unchanged -- it is one row per product per
-- provider either way.
-- The OLD keys first, and all three of them: each names `platform_id`, so each would otherwise
-- still hold the name the new key wants. Dropping the column drops its constraints too — but
-- these are added BEFORE that, since a unique key is what the merge infers its ON CONFLICT target
-- from and the window between must not exist.
ALTER TABLE platform.product_platforms DROP CONSTRAINT IF EXISTS product_platforms_unique;
ALTER TABLE platform.model_platforms DROP CONSTRAINT IF EXISTS model_platforms_unique;
ALTER TABLE platform.fee_package_platforms DROP CONSTRAINT IF EXISTS fee_package_platforms_unique;
DROP INDEX IF EXISTS platform.product_platforms_platform_idx;
DROP INDEX IF EXISTS platform.products_platform_id_idx;

ALTER TABLE platform.product_platforms
  ALTER COLUMN provider SET NOT NULL,
  ADD CONSTRAINT product_platforms_unique UNIQUE (product_id, provider);
CREATE INDEX product_platforms_provider_idx
  ON platform.product_platforms (provider, external_id);

-- The other two per-platform mappings the same way. Their unique keys named `platform_id` and so
-- were dropped WITH the column -- and the merge infers its ON CONFLICT target from the entity's
-- declared key, so without these it reports "no unique or exclusion constraint matching the ON
-- CONFLICT specification" and takes the whole entity down: a firm's models and its fee register,
-- read as "this firm has none".
ALTER TABLE platform.model_platforms
  ALTER COLUMN provider SET NOT NULL,
  ADD CONSTRAINT model_platforms_unique UNIQUE (model_id, provider);

ALTER TABLE platform.fee_package_platforms
  ALTER COLUMN provider SET NOT NULL,
  ADD CONSTRAINT fee_package_platforms_unique UNIQUE (fee_package_id, provider);

ALTER TABLE platform.product_platforms DROP COLUMN IF EXISTS platform_id;
ALTER TABLE platform.models            DROP COLUMN IF EXISTS platform_id;
ALTER TABLE platform.model_platforms   DROP COLUMN IF EXISTS platform_id;
ALTER TABLE platform.fee_packages      DROP COLUMN IF EXISTS platform_id;
ALTER TABLE platform.fee_package_platforms DROP COLUMN IF EXISTS platform_id;
ALTER TABLE platform.accounts          DROP COLUMN IF EXISTS primary_custodian;
ALTER TABLE crm.applications           DROP COLUMN IF EXISTS platform_id;

DROP TABLE platform.platforms;

-- Recreated against the enum'd columns. Unchanged otherwise: 0042's comment explains why
-- transactions and valuations carry their provider key in-row rather than as link rows.
CREATE VIEW platform.entity_links WITH (security_invoker = true) AS
  SELECT tenant_id, entity_type, entity_id, provider, external_id, link_type, updated_at
  FROM platform.links
  UNION ALL
  SELECT tenant_id, 'transaction', id, source_system, source_transaction_id, 'primary', updated_at
  FROM platform.transactions
  UNION ALL
  SELECT tenant_id, 'valuation', id, source_system,
         account_id || ':' || to_char(as_of_date, 'YYYY-MM-DD'), 'primary', updated_at
  FROM platform.valuations;

GRANT SELECT ON platform.entity_links TO authenticated, service_role;
