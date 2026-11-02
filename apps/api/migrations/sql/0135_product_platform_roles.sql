-- ============================================================================
-- 0135_product_platform_roles.sql -- which provider does WHAT for a product.
--
-- `docs/data-architecture.md` Decision 1 specifies a `platform.product_providers` table
-- "declaring which providers participate in a product and in what `role`
-- (custodian | scheme_administrator | wrapper_provider) with a `required` flag", so that "the
-- account inherits its sync targets from its product". **It was never built.** There is no such
-- table, and `platform.product_platforms` — the mapping that DID get built — carries the
-- provider's external reference and no role.
--
-- So today the role exists only at PROVIDER level, in `providers.PROVIDER_META['role']`. That is
-- enough to say "Morgan Lloyd administers schemes" and not enough to say the thing that matters:
-- **a SIPP custodied at SEI and administered by Morgan Lloyd cannot declare it.** Which is
-- precisely what "drive the provider calls from data" needs — `platform_actions.defaults_for`
-- should be able to ask "which providers does this account's product name, and in what role", and
-- there is nothing to ask.
--
-- ADDITIVE ON THE EXISTING MAPPING, not Decision 1's new table. `product_platforms` is already
-- keyed `(product_id, platform_id)`, which is exactly the grain a role belongs on, and it already
-- carries `external_id` (the value the vendor's API takes) and an allowlisted `data`
-- (`catalogue.PRODUCT_PLATFORM_SETTINGS`). A second table would give one concept two homes and
-- leave every reader deciding which to trust. Decision 1's intent is met; its table is not built,
-- and that supersession is recorded in that document.
--
-- THE BACKFILL IS THE PROVIDER'S OWN ROLE, which is right for every row that exists: a product
-- mapped to Seccl or SEI is custodied there, and one mapped to Morgan Lloyd is administered there.
-- More than one mapping may legitimately carry the same role: 0042 deliberately made one shelf
-- item available on several custodians, and the account's own provider/link decides where that
-- particular account is held. The role classifies a mapping; it does not make the mapping unique.
--
-- `required` defaults FALSE, deliberately. A true default would make every existing mapping a
-- prerequisite for opening an account, and the estate has mappings that are discovery artefacts
-- (`_PRODUCTS` is insert-only from the accounts a firm already holds). Requiredness is a firm's
-- statement about its own shelf, so it starts unstated.
-- ============================================================================

-- `required` IS NOT ADDED. It was, and 0137 dropped it seven migrations later with the note "no
-- writer, no reader, no test: it appears only in the migration that created it" — which was true
-- the day it was written. A fresh database rewrote this table twice to arrive where it started,
-- and a reader met two contradictory rationales for one column. `role` IS written (by
-- `catalogue.set_product_platform` and by the sink's `PROVIDER_ROLES`) and read (by
-- `GET /catalog/products`' custodian preference), which is the difference between the two.
ALTER TABLE platform.product_platforms
  ADD COLUMN IF NOT EXISTS role text;

ALTER TABLE platform.product_platforms
  ADD CONSTRAINT product_platforms_role_check
  CHECK (role IS NULL OR role IN ('custodian', 'scheme_administrator', 'wrapper_provider'));

COMMENT ON COLUMN platform.product_platforms.role IS
  'What this provider DOES for this product: custodian (holds the assets) | '
  'scheme_administrator (administers the scheme — a SIPP''s Morgan Lloyd leg) | '
  'wrapper_provider. Decision 1''s platform.product_providers, built here instead of as a second '
  'table. It is what lets one product name a custodian AND an administrator, which is the '
  'SEI-custodied ML-administered SIPP the estate actually runs.';

-- ---- Backfill ----
--
-- As service_role: `platform.product_platforms` has forced RLS with a policy for `authenticated`
-- only, and `migrator` is not bypassrls — run as migrator this UPDATE matches zero rows and
-- succeeds silently. The trap 0042's seed, 0059's backfill and 0118's all document.
SET LOCAL ROLE service_role;

UPDATE platform.product_platforms pp
   SET role = CASE p.provider
                WHEN 'morgan_lloyd' THEN 'scheme_administrator'
                WHEN 'seccl'        THEN 'custodian'
                WHEN 'sei'          THEN 'custodian'
              END
  FROM platform.platforms p
 WHERE p.id = pp.platform_id
   AND pp.role IS NULL
   AND p.provider IN ('morgan_lloyd', 'seccl', 'sei');

RESET ROLE;
