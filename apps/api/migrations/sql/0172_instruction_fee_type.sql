-- 0172_instruction_fee_type — WHICH fee an ad hoc fee is.
--
-- `adhoc_fee` (0154) recorded an amount and nothing else, so a firm running an ongoing adviser
-- charge, an investment-management charge and an initial charge had one undifferentiated row for
-- all three: the instruction history could say £250 left the account and never what for, and
-- nobody downstream could tell two charges apart to bill, report or reverse one.
--
-- The vocabulary is the FIRM'S OWN fee register (`platform.fee_packages`), not a new enum. The
-- names a firm charges under are the names it has already configured — inventing a closed list
-- here would be a second, disagreeing answer to a question the catalogue already answers, and the
-- one the adviser recognises is the firm's.
--
-- A REFERENCE, not a copy of the rule. The package says what is normally charged; an ad hoc fee
-- is by definition a one-off amount the adviser types, so the package is what KIND of charge this
-- is and never what it costs.
--
-- The composite (tenant_id, fee_package_id) foreign key makes tenant containment a schema
-- invariant rather than a check the write path has to remember — this row is written by
-- `firm_session`, which is service_role and therefore unbound by RLS. `SET NULL (fee_package_id)`
-- names the column: the plain form would null `tenant_id` too, which is NOT NULL, so the delete
-- would fail instead of the reference clearing.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.instructions
  ADD COLUMN fee_package_id int,
  ADD CONSTRAINT instructions_fee_package_tenant_fkey
    FOREIGN KEY (tenant_id, fee_package_id)
    REFERENCES platform.fee_packages (tenant_id, id)
    ON DELETE SET NULL (fee_package_id),
  -- A fee type belongs to a fee. A withdrawal or a model movement carrying one would render as a
  -- charge in the history it appears in.
  ADD CONSTRAINT instructions_fee_package_is_a_fee
    CHECK (fee_package_id IS NULL OR kind = 'adhoc_fee');

CREATE INDEX instructions_fee_package_id_idx
  ON platform.instructions (fee_package_id)
  WHERE fee_package_id IS NOT NULL;

COMMENT ON COLUMN platform.instructions.fee_package_id IS
  'Which of the firm''s fee packages this ad hoc fee is charged under — the KIND of charge, never '
  'its price (the amount is typed). Null on every other kind, and on a fee raised by a firm whose '
  'register is empty.';
