-- An optional adviser fee on a payment in. The typed amount is what the client pays; this is
-- taken off it in the same Seccl portfolio-transaction group so the two legs share an order.
-- Not the provider rail fee (`rail_fee`, withdrawals only) and not an ad hoc fee instruction
-- of its own — a standalone `adhoc_fee` still spends existing cash.
-- The kind check is `instruction_kinds.FEE_WITH_PAYMENT` (`expected_in` only).

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.instructions
  ADD COLUMN adviser_fee numeric(18,2) NOT NULL DEFAULT 0,
  ADD CONSTRAINT instructions_adviser_fee_nonnegative CHECK (adviser_fee >= 0),
  ADD CONSTRAINT instructions_adviser_fee_payment_only
    CHECK (adviser_fee = 0 OR kind = 'expected_in');

COMMENT ON COLUMN platform.instructions.adviser_fee IS
  'Adviser charge taken from a payment in; instructed as a second Seccl group member. Zero outside expected_in.';
