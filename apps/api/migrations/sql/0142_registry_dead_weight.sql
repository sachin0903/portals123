-- ============================================================================
-- 0142_registry_dead_weight -- two join tables that were never written, and eight columns with
-- no reader, no writer, or neither.
--
-- Each of these was found by asking the same question of the whole registry layer: who writes
-- this, and who reads what they wrote. The answers below are counts from the dev book, not
-- inferences.
--
-- ---------------------------------------------------------------------------
-- THE TWO JOIN TABLES: 0 ROWS ON EVERY DEPLOYMENT.
-- ---------------------------------------------------------------------------
-- `transfer_provider_products` and `third_party_provider_products` (0042) each have exactly one
-- writer, `catalogue.set_provider_products`, reached only when a request carries `product_ids`.
--
--   * For `transfer`, NOTHING EVER SENDS IT. The register's own editor posts `kind, id, name,
--     enabled` and no product list; the pipeline registers `transfer_providers` with no child
--     entity. And it did not need to: `0046` added `transfer_providers.products text[]` with a
--     GIN index for precisely this question ("who can take an ISA transfer"), it is populated for
--     668 of 668 rows, and `catalogue.providers` already filters and facets on it. The join table
--     is the same fact modelled a second time, and the second model is the empty one.
--
--   * For `third_party`, the admin checkboxes DO reach it -- and they are the only thing that
--     ever has, so the register's own statement of what each provider provides has been ignored.
--     The conformed model publishes `product_key` (Seccl's `wrapper_type`) and says in its own
--     comment that it exists "so a third-party provider can be attached to the product it
--     actually provides"; the staging projection then drops the column. Deriving it is not a new
--     source of truth, it is the one that was already there.
--
-- ---------------------------------------------------------------------------
-- THE COLUMNS.
-- ---------------------------------------------------------------------------
--   products.data                   no writer and no reader. `_PRODUCT_FIELDS` excludes it and
--                                   so does the pipeline's projection. (`pp.data` on
--                                   product_platforms is a different column and STAYS -- it
--                                   carries ml_scheme_type/ml_sipp_type, which sync.py reads.)
--   products.platform_identifier    written by the pipeline, read by nobody, and duplicating
--                                   `product_platforms.external_id` value for value (SIPP holds
--                                   'PENSION' in both). `0041` states these columns are
--                                   superseded by the join tables and `0063` DROPPED the
--                                   `platform.models` twin on exactly that reasoning.
--                                   NOTE: `platform.accounts.platform_identifier` is unrelated
--                                   and alive -- it is the account number at the custodian.
--   products.platform_id            0002's, made nullable by 0042/0044 and written by nothing
--                                   since 0044 moved the reference to the child. NULL on every
--                                   row of a real book -- and an INNER JOIN on it is what made
--                                   `GET /catalog/products` answer "nothing" for a firm with
--                                   seven products.
--   product_platforms.required      0122's. No writer, no reader, no test: it appears only in
--                                   the migration that created it.
--   product_platforms.external_type accepted by `set_product_platform` and typed in the admin
--                                   client, but no form control posts it and no pipeline writes
--                                   it -- plumbing with no source at either end.
--   platforms.config                0002's. Written only by a dev seed, read by nobody.
--
-- Dropping a column is not expand/contract-safe on its own, so each is dropped only because
-- nothing reads it: an older API instance still running against this schema cannot notice.
-- ============================================================================

DROP TABLE IF EXISTS platform.transfer_provider_products;
DROP TABLE IF EXISTS platform.third_party_provider_products;

-- WHAT EACH THIRD-PARTY PROVIDER PROVIDES, from the register's own answer.
--
-- TEXT, not `platform.product_type`, and for the reason `transfer_providers.products` is text:
-- a wrapper we do not model is still worth showing. An enum would make the register refuse to
-- publish a provider whose product line the shelf has no member for, which is a worse answer
-- than naming it.
ALTER TABLE platform.third_party_providers
  ADD COLUMN IF NOT EXISTS product text;

COMMENT ON COLUMN platform.third_party_providers.product IS
  'The wrapper this provider provides, from the register''s own `data->>''wrapper_type''` '
  '(Seccl''s vocabulary: TPPENSION, TPOFFBOND, PENSION). Aliased through PRODUCT_ALIASES on the '
  'way in, so PENSION lands as SIPP -- the same hop the account and product entities make, and '
  'skipping it left 2 of 7 rows naming a wrapper the shelf does not have. Replaces '
  'third_party_provider_products (0042), which was populated by hand through admin checkboxes '
  'and held 0 rows everywhere.';

-- SET LOCAL ROLE service_role, OR THIS BACKFILL IS A NO-OP THAT REPORTS SUCCESS. The migrator
-- owns the table but is not BYPASSRLS, and FORCE ROW LEVEL SECURITY binds the owner too, so the
-- UPDATE would match the rows its (absent) request.jwt.claims allows -- none. 0059 documented
-- this for platform.products and it has been hit three times since (0127, 0128, and again here
-- in review).
SET LOCAL ROLE service_role;

UPDATE platform.third_party_providers
   SET product = CASE upper(data ->> 'wrapper_type')
                   -- PRODUCT_ALIASES = {"PENSION": "SIPP"}. Declared, never a general
                   -- normaliser: an unlisted wrapper is stored as stated rather than guessed at.
                   WHEN 'PENSION' THEN 'SIPP'
                   ELSE upper(data ->> 'wrapper_type')
                 END
 WHERE product IS NULL
   AND data ->> 'wrapper_type' IS NOT NULL;

RESET ROLE;

ALTER TABLE platform.products
  DROP COLUMN IF EXISTS data,
  DROP COLUMN IF EXISTS platform_identifier,
  DROP COLUMN IF EXISTS platform_id;

ALTER TABLE platform.product_platforms
  -- `required` is no longer added by 0130, so there is nothing here to drop.
  DROP COLUMN IF EXISTS external_type;

ALTER TABLE platform.platforms
  DROP COLUMN IF EXISTS config;
