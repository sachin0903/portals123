-- Whether an account may hold withdrawal destination bank details is a product decision.
-- A JISA cannot be withdrawn from before conversion to an ISA, so existing JISA products are
-- disabled. Other products retain the current behaviour until the firm narrows them.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.products
  ADD COLUMN bank_details_allowed boolean NOT NULL DEFAULT true;

COMMENT ON COLUMN platform.products.bank_details_allowed IS
  'Whether accounts of this product may hold withdrawal destination bank details.';

SET LOCAL ROLE service_role;
UPDATE platform.products
   SET bank_details_allowed = false
 WHERE product_type = 'JISA';
RESET ROLE;
