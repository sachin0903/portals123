-- 0265_adviser_fee_type — WHICH fee an adviser charge on a payment or withdrawal is.
--
-- 0172 gave an ad hoc fee a `fee_package_id` (the KIND of charge) and reserved it for `adhoc_fee`
-- only, because a fee type belongs to a fee and a payment is not a charge. But an adviser fee that
-- rides a payment or a withdrawal IS a charge, and SEI will not take one without naming a fee TYPE
-- (a configured fee profile / feeTypeId) — there is no bare amount or percentage. So the adviser-fee
-- component gets its OWN reference, separate from `fee_package_id`, so the movement stays a movement
-- and only its fee names a type.
--
-- Same tenant-containment FK as 0172, and ON DELETE SET NULL names the column so a fee package can
-- be retired without the NOT NULL tenant_id blocking the delete.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.instructions
  ADD COLUMN adviser_fee_package_id int,
  ADD CONSTRAINT instructions_adviser_fee_package_tenant_fkey
    FOREIGN KEY (tenant_id, adviser_fee_package_id)
    REFERENCES platform.fee_packages (tenant_id, id)
    ON DELETE SET NULL (adviser_fee_package_id),
  ADD CONSTRAINT instructions_adviser_fee_package_movement_only
    CHECK (adviser_fee_package_id IS NULL
           OR kind IN ('expected_in', 'withdrawal', 'model_withdrawal'));

CREATE INDEX instructions_adviser_fee_package_id_idx
  ON platform.instructions (adviser_fee_package_id)
  WHERE adviser_fee_package_id IS NOT NULL;

COMMENT ON COLUMN platform.instructions.adviser_fee_package_id IS
  'Which of the firm''s fee packages the adviser fee on this payment or withdrawal is charged '
  'under — the fee TYPE SEI needs (feeTypeId), never its price (the amount is adviser_fee). Null '
  'on an ad hoc fee (which uses fee_package_id) and on a movement with no adviser fee.';
