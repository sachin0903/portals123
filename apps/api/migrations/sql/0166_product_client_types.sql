-- The contact types permitted to own each shelf product. Existing flexible products keep
-- both contact types; wrappers that can only belong to a person are narrowed to individuals.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.products
  ADD COLUMN allowed_client_types crm.contact_type[] NOT NULL
    DEFAULT ARRAY['individual', 'organisation']::crm.contact_type[],
  ADD CONSTRAINT products_allowed_client_types_not_empty
    CHECK (cardinality(allowed_client_types) > 0);

COMMENT ON COLUMN platform.products.allowed_client_types IS
  'Contact types permitted to own this product at journey kickoff; never empty.';

SET LOCAL ROLE service_role;
UPDATE platform.products
   SET allowed_client_types = ARRAY['individual']::crm.contact_type[]
 WHERE product_type IN ('ISA', 'JISA');
RESET ROLE;
