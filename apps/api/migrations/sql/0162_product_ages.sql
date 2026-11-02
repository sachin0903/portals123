-- Owner age limits used by journey kickoff. Null means no bound. JISA is seeded
-- at max_age = 17: the owner must be strictly under 18 on the journey start date.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.products
  ADD COLUMN min_age smallint,
  ADD COLUMN max_age smallint,
  ADD CONSTRAINT products_age_nonnegative
    CHECK (
      (min_age IS NULL OR min_age >= 0)
      AND (max_age IS NULL OR max_age >= 0)
    ),
  ADD CONSTRAINT products_age_range
    CHECK (min_age IS NULL OR max_age IS NULL OR min_age <= max_age);

COMMENT ON COLUMN platform.products.min_age IS
  'Minimum completed years of age the account owner must have on the journey start date; null means no floor.';
COMMENT ON COLUMN platform.products.max_age IS
  'Maximum completed years of age the account owner may have on the journey start date; null means no ceiling. JISA is 17 (strictly under 18).';

SET LOCAL ROLE service_role;
UPDATE platform.products
   SET max_age = 17
 WHERE product_type = 'JISA'
   AND max_age IS NULL;
RESET ROLE;
