-- A withdrawal instruction records the payment rail's own charge separately from the amount the
-- client receives. Both are claims on account cash while the instruction is pending. Keeping the
-- fee on the intent makes the reservation stable if the destination bank details or deployment
-- pricing change before settlement.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.instructions
  ADD COLUMN rail_fee numeric(18,2) NOT NULL DEFAULT 0,
  ADD CONSTRAINT instructions_rail_fee_nonnegative CHECK (rail_fee >= 0),
  ADD CONSTRAINT instructions_rail_fee_withdrawal_only
    CHECK (rail_fee = 0 OR kind = 'withdrawal');

COMMENT ON COLUMN platform.instructions.rail_fee IS
  'Provider payment-rail charge reserved in addition to amount; zero outside withdrawals.';
