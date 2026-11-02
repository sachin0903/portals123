-- ============================================================================
-- 0176_transfer_pension_facts.sql — crystallisation, ceding pension type, safeguarded rights.
--
-- `cam_pensiontype` is Morgan Lloyd's Yes* on Create Transfer: required only for a pension
-- policy, and its option set is the CEDING scheme's product type (AVC, SIPP, SSAS…). It lived
-- in `platform.transfers.data` because the raise drawer never asked, so a normally raised
-- pension transfer could not be pushed. The drawer now asks, so it is a COLUMN — the same move
-- 0148 made for basis/extent.
--
-- Crystallisation is a STATUS, not two money boxes. Uncrystallised (accumulation) / partial /
-- crystallised maps onto the split ML requires: the crystallised amount is asked only when some
-- of the pot has been crystallised, and the uncrystallised remainder is derived so the three
-- figures cannot disagree.
--
-- Safeguarded rights are the Pension Schemes Act 2015 s.48(8) question. Morgan Lloyd has no
-- field for it; we hold it because the adviser has to have answered it, not because a vendor
-- asked.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.transfers
  ADD COLUMN IF NOT EXISTS crystallisation text,
  ADD COLUMN IF NOT EXISTS pension_type smallint,
  ADD COLUMN IF NOT EXISTS safeguarded_benefits boolean;

ALTER TABLE platform.transfers
  DROP CONSTRAINT IF EXISTS transfers_crystallisation_check,
  DROP CONSTRAINT IF EXISTS transfers_pension_type_check;

ALTER TABLE platform.transfers
  ADD CONSTRAINT transfers_crystallisation_check
    CHECK (crystallisation IS NULL OR crystallisation IN (
      'uncrystallised', 'partial', 'crystallised'
    )),
  ADD CONSTRAINT transfers_pension_type_check
    CHECK (pension_type IS NULL OR pension_type BETWEEN 1 AND 20);

COMMENT ON COLUMN platform.transfers.crystallisation IS
  'Whether the transferring pension is uncrystallised (accumulation), partially crystallised, '
  'or crystallised. The crystallised / uncrystallised amounts are derived from this plus the '
  'crystallised amount, so they cannot disagree with the total.';
COMMENT ON COLUMN platform.transfers.pension_type IS
  'The ceding scheme''s product type, Morgan Lloyd cam_pensiontype (1–20). Required to record a '
  'pension transfer at the administrator.';
COMMENT ON COLUMN platform.transfers.safeguarded_benefits IS
  'Whether the transferring pension contains safeguarded rights as defined in s.48(8) of the '
  'Pension Schemes Act 2015. Asked of the adviser; not a vendor field.';
