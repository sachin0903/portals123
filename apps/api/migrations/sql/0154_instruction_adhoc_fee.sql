-- 0154_instruction_adhoc_fee: a one-off fee is a cash instruction, like a withdrawal without a
-- destination bank.
--
-- Widening the CHECK rather than a new table: the row is the same shape (account, amount, pending)
-- and the gate is the same cash leg. The name is `adhoc_fee` because `fee` already means a fee
-- package on the account.

ALTER TABLE platform.instructions DROP CONSTRAINT IF EXISTS instructions_kind_check;
ALTER TABLE platform.instructions
  ADD CONSTRAINT instructions_kind_check
  CHECK (kind IN ('expected_in', 'withdrawal', 'adhoc_fee', 'model_topup', 'model_withdrawal'));
