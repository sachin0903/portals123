-- ============================================================================
-- 0264_withdrawal_fee_and_disinvest.sql -- an adviser fee and an auto-disinvest can ride a
-- WITHDRAWAL, not just a payment.
--
-- Until now an adviser fee was payment-only (0245: `adviser_fee = 0 OR kind = 'expected_in'`). A
-- withdrawal can now carry one too. The semantics differ: on a payment IN the fee is taken OFF the
-- money arriving (net lands); on a withdrawal it is an ADDITIONAL debit alongside the money going
-- out, so the "fee < amount" rule that applies to a payment does not apply here (the write path
-- enforces the per-kind rules in `instructions._adviser_fee`).
--
-- `auto_disinvest` is the "sell down holdings to fund this" option (SEI's raise-cash,
-- `raiseCashMethodOptions`; Seccl's `autoDisinvest`). Only a withdrawal raises cash — a payment in
-- has nothing to sell — so it is constrained to the money-out kinds.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.instructions
  DROP CONSTRAINT instructions_adviser_fee_payment_only,
  ADD CONSTRAINT instructions_adviser_fee_movement_only
    CHECK (adviser_fee = 0 OR kind IN ('expected_in', 'withdrawal', 'model_withdrawal')),
  ADD COLUMN auto_disinvest boolean NOT NULL DEFAULT false,
  ADD CONSTRAINT instructions_auto_disinvest_withdrawal_only
    CHECK (auto_disinvest = false OR kind IN ('withdrawal', 'model_withdrawal'));

COMMENT ON COLUMN platform.instructions.auto_disinvest IS
  'Sell down holdings to fund this withdrawal (SEI raise-cash / Seccl autoDisinvest). False, and '
  'only ever true on a withdrawal or model withdrawal.';
