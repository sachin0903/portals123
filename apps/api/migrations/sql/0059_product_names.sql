-- platform.products: a readable `name`, and the wrapper code in `code`.
--
-- `_sync_products` discovers a firm's shelf from the wrappers it holds accounts in, and wrote the
-- enum member into BOTH the product's identity fields' places: `name = 'TPONBOND'` and `code`
-- left NULL. So a firm's product list read "TPONBOND" as the product's name and had no code at
-- all -- the two the wrong way round. `name` is what a person reads; `code` is what an API call
-- carries.
--
-- Only rows where `name` STILL EQUALS the product type are touched. That is the discovery
-- placeholder; anything else is a name a firm chose, and this migration must not overrule it.
-- `code` is filled only where absent, for the same reason.
--
-- The labels match `app/materialise.py::PRODUCT_LABELS`, which now writes them on insert, so a
-- freshly discovered shelf and a backfilled one read identically.
--
-- As service_role: platform.products has FORCE RLS and a SELECT-only policy for `authenticated`,
-- and the migrator role is not BYPASSRLS -- the same trap 0042's own seed documents.
SET LOCAL ROLE service_role;

UPDATE platform.products p
   SET name = v.label,
       code = coalesce(p.code, v.code)
  FROM (VALUES
          ('GIA',       'General Investment Account'),
          ('ISA',       'Individual Savings Account'),
          ('JISA',      'Junior Individual Savings Account'),
          ('SIPP',      'Self-Invested Personal Pension'),
          ('TPONBOND',  'Third-party Onshore Bond'),
          ('TPOFFBOND', 'Third-party Offshore Bond'),
          ('TPPENSION', 'Third-party Pension')
       ) AS v (code, label)
 WHERE p.product_type::text = v.code
   -- The discovery placeholder, and nothing else.
   AND p.name = v.code;

-- A product whose name a firm changed but which still carries no code: the code is the wrapper's
-- identity, not a preference, so it is safe to fill on its own.
UPDATE platform.products
   SET code = product_type::text
 WHERE code IS NULL;

RESET ROLE;

COMMENT ON COLUMN platform.products.name IS
  'What the firm calls this wrapper -- readable. Seeded from the canonical label on discovery '
  '(app/materialise.py::PRODUCT_LABELS) and never overwritten by a later sync.';
COMMENT ON COLUMN platform.products.code IS
  'The wrapper code -- the platform.product_type member, which is what an API call carries. The '
  'per-platform reference (a Seccl clientProductId) lives on platform.product_platforms instead.';
