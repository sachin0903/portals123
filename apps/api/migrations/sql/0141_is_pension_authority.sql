-- ============================================================================
-- 0141_is_pension_authority -- `platform.products.is_pension` is what shows the client record's
-- pension sections, so its own comment stops telling readers not to trust it.
--
-- 0128 corrected the column (it had been NOT NULL DEFAULT false and never written, so it was
-- false for every pension since 0042) and then said, accurately for the state it found: "five
-- surfaces decide pension-ness from product_type, so this is not the authority ... do not read it
-- in preference to product_type."
--
-- WHY THAT IS NOW WRONG. The wrapper set those surfaces test has NINE COPIES and they disagree:
-- `contributions`, `matching`, `beneficiaries` and `accounts/transactions.ts` carry
-- {SIPP, PENSION, SSAS, TPPENSION}; `routers/crm`, `routers/illustrations` and
-- `morgan_lloyd/sync` omit TPPENSION; `flows/planning` tests {SIPP, TPPENSION} plus a substring
-- match. So which sections a client's record showed depended on which copy the component
-- happened to import, and a TPPENSION client could take a contribution and nominate a
-- beneficiary while the record declined to ask for the earnings figure the relievable-limit
-- check needs. A column a firm can see and correct beats nine literals it cannot.
--
-- WHAT DID NOT MOVE, deliberately. The INTEGRATION gates keep their own sets, because they
-- answer a different question: `cam_createscheme` excluding TPPENSION is CORRECT — a third-party
-- pension is administered elsewhere and is not a Morgan Lloyd scheme — and the ML
-- policy-valuation and drawdown gates are that same call in another costume. Consolidating them
-- into `is_pension` would file schemes at a custodian that does not hold them. What is SHOWN on a
-- record and what is SENT to a custodian are separate decisions; only the first is a display
-- question, and only the first moved (docs/client-record.md §3).
--
-- Comment-only. No DDL, no DML, so none of the FORCE-RLS / service_role hazard applies.
-- ============================================================================

COMMENT ON COLUMN platform.products.is_pension IS
  'Whether this wrapper is a pension. THE AUTHORITY for what a firm is SHOWN -- the client '
  'record''s Pension and Employer sections and the account record''s Beneficiaries panel read it, '
  'in preference to product_type, because the wrapper set they used to test has nine '
  'disagreeing copies in the codebase. Seeded from product_type by 0128 and editable per firm on '
  'the product page, which is the point: a bond a firm treats as a pension wrapper can say so. '
  'It is NOT what decides a call to a custodian -- see 0129''s header for why cam_createscheme '
  'keeps its own narrower set.';
