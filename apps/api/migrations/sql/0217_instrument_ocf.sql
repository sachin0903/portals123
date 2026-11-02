-- The fund's ongoing charge, from the custodian's costs-and-charges file.
-- SEI publishes ONGOING_CHARGE_ACTUAL (else the estimate, when the firms that
-- published one published the same number). Stored as published: 0.77 stays 0.77.
-- Nullable. A blank file is not a zero charge.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.instruments
  ADD COLUMN ocf numeric(18, 10);

COMMENT ON COLUMN platform.instruments.ocf IS
  'Ongoing charge as the custodian published it. SEI: CostsCharges actual, or the '
  'estimate when no actual was published and the firms agree. Not rescaled.';
