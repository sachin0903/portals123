-- ============================================================================
-- 0130_valuation_pension_split.sql -- the crystallisation split, and cash held at a SECOND place.
--
-- Morgan Lloyd's `cam_getscheme` answers a scheme with six figures. Three of them we already have
-- somewhere; three are new; and knowing WHICH is which is the whole design, because for a SIPP
-- Morgan Lloyd is not the custodian:
--
--   crystallisedFundAmount    NEW -- ML computes the crystallisation split. We do not.
--   uncrystallisedFundAmount  NEW
--   totalBankBalance          NEW -- the SCHEME's own bank account, at ML, which is NOT the cash
--                                    in the custodian's account. This is the figure a client
--                                    cannot currently see anywhere.
--   the three fee percentages      already on the firm's fee package
--
-- **THE FUND TOTAL IS OUR OWN NUMBER COMING BACK.** We PUSH the valuation to Morgan Lloyd
-- (`cam_createpolicyvaluation`, `app/integrations/morgan_lloyd/valuations.py`) from the
-- custodian's holdings; their `crystallisedFundAmount + uncrystallisedFundAmount` is therefore
-- the figure we last sent them, re-derived. It is not an independent valuation and must never be
-- read as a second opinion.
--
-- THE CONSEQUENCE, AND IT IS THE TRAP IN THIS MIGRATION: `platform.valuations` is keyed
-- (account_id, as_of_date, source_system), so one pension account can hold a Seccl/SEI row AND a
-- morgan_lloyd row for the same date. Summing `market_value` across source systems DOUBLE-COUNTS
-- the investments — the custodian values the assets, and ML's fund figure IS those assets.
-- Summing `cash_value` across them is the opposite: it is exactly right, and it is the point.
-- A client's money sits in two places and they can presently see only one of them.
--
-- So: read a VALUE from one source system, and read CASH from all of them. Pinned by
-- `pkgs/book-sink/tests` and by `apps/api/tests/test_valuation_sources.py`.
--
-- Nullable, because only Morgan Lloyd publishes a crystallisation split: no other custodian in
-- the estate models it (SEI's environment defines no pension WRAPPER; its SIPPs are Investment
-- Accounts classified as pensions, and SEI is their custodian, not their administrator), and a NOT NULL
-- default of 0 would state that every Seccl account is wholly uncrystallised — which is a claim
-- about a client's pension, not a missing value.
-- ============================================================================

ALTER TABLE platform.valuations
  ADD COLUMN IF NOT EXISTS crystallised_value   numeric(20, 4),
  ADD COLUMN IF NOT EXISTS uncrystallised_value numeric(20, 4);

COMMENT ON COLUMN platform.valuations.crystallised_value IS
  'The crystallised slice of a pension''s fund value, as the scheme administrator computes it '
  '(ML cam_getscheme). NULL for a provider that does not model crystallisation — never 0, which '
  'would assert the fund is wholly uncrystallised.';
COMMENT ON COLUMN platform.valuations.uncrystallised_value IS
  'The uncrystallised slice. crystallised + uncrystallised is the fund total the scheme '
  'administrator holds, which for a SIPP is the valuation WE pushed them — not an independent '
  'figure, and not to be summed with the custodian''s own market_value.';

COMMENT ON COLUMN platform.valuations.cash_value IS
  'Cash at THIS source system on this date. Deliberately summable ACROSS source systems, unlike '
  'market_value: a pension''s cash sits both in the custodian''s account and in the scheme''s own '
  'bank account at the administrator, and the client can otherwise see only the first.';
