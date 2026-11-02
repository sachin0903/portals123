-- Product-level rules used by journey kickoff. A product, not a flow or an
-- application, decides where it may be custodied and how many parties an
-- account may carry.
--
-- Existing products retain the old custody behaviour (either location) and
-- take the agreed two-party limit. Both columns are defaulted so provider
-- discovery can continue inserting a minimal shelf row safely.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.products
  ADD COLUMN allowed_custodies platform.custody_type[] NOT NULL
    DEFAULT ARRAY['onshore', 'offshore']::platform.custody_type[],
  ADD COLUMN max_parties smallint NOT NULL DEFAULT 2,
  ADD CONSTRAINT products_allowed_custodies_not_empty
    CHECK (cardinality(allowed_custodies) > 0),
  ADD CONSTRAINT products_max_parties_positive
    CHECK (max_parties >= 1);

COMMENT ON COLUMN platform.products.allowed_custodies IS
  'Custody locations this shelf product permits; journey kickoff offers only these values.';
COMMENT ON COLUMN platform.products.max_parties IS
  'Maximum account_contacts that may be assigned to one account at journey kickoff.';
