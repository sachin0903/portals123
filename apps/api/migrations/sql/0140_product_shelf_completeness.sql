-- ============================================================================
-- 0140_product_shelf_completeness.sql -- the two wrappers no firm could offer, and the pension
-- flag that was false for every pension.
--
-- ---------------------------------------------------------------------------
-- THE MISSING WRAPPERS, AND WHY THEY WERE MISSING.
-- ---------------------------------------------------------------------------
--
-- `platform.product_type` has carried seven members since 0002 and `PRODUCT_LABELS`
-- (`pkgs/book-sink`) names all seven, but a firm's shelf only ever gained a row when the provider
-- pipeline DISCOVERED an account using that wrapper. The dev book holds GIA, ISA, JISA, SIPP and
-- TPPENSION accounts, so those five products exist and **TPONBOND and TPOFFBOND do not** -- for
-- every tenant, on every deployment.
--
-- That is discovery working as designed and a shelf that is wrong all the same: a product is what
-- a firm OFFERS, and the first client to want a third-party bond needs the wrapper to exist BEFORE
-- there is an account using it. Worse, the absence is self-perpetuating -- no product means the
-- merge rejects any account that arrives with that wrapper ("no product parent"), which reads as
-- "this firm has no data here" rather than as a missing shelf item.
--
-- Seeded for every tenant that already has a catalogue, not for the model tenant and not globally:
-- `products_tenant_type_key` is `(tenant_id, product_type) WHERE tenant_id IS NOT NULL`, so a
-- tenant-scoped shelf is the only shape the index will hold, and a tenant with no products at all
-- has not been provisioned yet -- giving it two would be the only two it had.
--
-- LABELS MATCH `PRODUCT_LABELS`, hyphen included. The estate already stores "Third-party Pension",
-- so "Third Party Onshore Bond" beside it would put two spellings of one prefix in one dropdown.
-- The label is also the only thing `materialise.PRODUCT_LABELS` will not overwrite once a firm has
-- renamed it, so getting it right here is getting it right for good.
-- ============================================================================

-- AS service_role, and this is not optional. `platform.products` has FORCE ROW LEVEL SECURITY
-- with a SELECT-only policy for `authenticated`, and the migrator is not BYPASSRLS -- so both
-- statements below run against a table the migrator can see NO rows in. The INSERT ... SELECT
-- would insert nothing (its own SELECT is filtered to empty) and the UPDATE would touch nothing,
-- and BOTH would report success. 0059 documents the same trap on the same table, and 0127 hit it
-- again on `crm.contact_notes` -- where the silent no-op then failed a NOT NULL that validates
-- every row regardless of policy. THE RULE: any DML in a migration over a FORCE-RLS table needs
-- this line, or it is a no-op that passes.
SET LOCAL ROLE service_role;

INSERT INTO platform.products (tenant_id, product_type, code, name, currency, is_pension, enabled)
SELECT DISTINCT p.tenant_id, w.product_type, w.code, w.name, 'GBP', false, true
  FROM platform.products p
  CROSS JOIN (VALUES
      ('TPONBOND'::platform.product_type,  'TPONBOND',  'Third-party Onshore Bond'),
      ('TPOFFBOND'::platform.product_type, 'TPOFFBOND', 'Third-party Offshore Bond')
    ) AS w(product_type, code, name)
 WHERE p.tenant_id IS NOT NULL
ON CONFLICT (tenant_id, product_type) WHERE tenant_id IS NOT NULL DO NOTHING;

-- ---------------------------------------------------------------------------
-- `is_pension` WAS FALSE FOR EVERY PENSION.
-- ---------------------------------------------------------------------------
--
-- The column is NOT NULL with a `false` default and nothing has ever written it, so the SIPP and
-- the third-party pension on every shelf claimed not to be pensions. Nothing broke, and that is
-- the point: five surfaces decide pension-ness from `product_type` instead
-- (`contributions.PENSION_PRODUCTS`, `matching`, `beneficiaries`, `flows.planning._is_pension`,
-- and the portal's `isPensionProduct`), so the column has been a second, wrong answer to a
-- question nobody asked it.
--
-- CORRECTED RATHER THAN DROPPED, and only just: a derived column that disagrees with its own
-- derivation is exactly what `docs/data-architecture.md` says not to keep. It survives because it
-- is the one place a firm could eventually say "this bond is a pension wrapper for our purposes"
-- when the wrapper alone cannot answer -- and because dropping it is a separate decision from
-- fixing it (see the note in `docs/client-record.md`). Until something writes it deliberately,
-- `product_type` stays the authority and this is kept in step with it.
UPDATE platform.products
   SET is_pension = (upper(product_type::text) IN ('SIPP', 'TPPENSION', 'SSAS'))
 WHERE is_pension IS DISTINCT FROM (upper(product_type::text) IN ('SIPP', 'TPPENSION', 'SSAS'));

RESET ROLE;

COMMENT ON COLUMN platform.products.is_pension IS
  'Whether this wrapper is a pension. DERIVED from product_type today and kept in step with it by '
  '0128 -- five surfaces decide pension-ness from product_type, so this is not the authority. It '
  'exists for the case product_type cannot answer (a bond a firm treats as a pension wrapper); '
  'until something writes it deliberately, do not read it in preference to product_type.';
