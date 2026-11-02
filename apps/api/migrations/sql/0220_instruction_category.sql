-- A pension payment records which contribution it is. That category selects the
-- firm's SEI treatment code. A GIA payment leaves it unset.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.instructions
  ADD COLUMN category text;

ALTER TABLE platform.instructions
  ADD CONSTRAINT instructions_category_chk
  CHECK (
    category IS NULL
    OR category IN (
      'personal_contribution',
      'employer_contribution',
      'third_party_contribution'
    )
  );

COMMENT ON COLUMN platform.instructions.category IS
  'Contribution category on a pension payment: personal, employer, or third party. '
  'Null when the payment is not a contribution.';
