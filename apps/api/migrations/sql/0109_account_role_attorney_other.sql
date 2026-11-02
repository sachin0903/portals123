-- 0109_account_role_attorney_other — interested-party roles for kickoff.
--
-- crm.account_role was ('owner','decision_maker','trustee','beneficiary'). Firms need to record
-- an attorney (power of attorney) and a catch-all "other" at journey kickoff without inventing a
-- parallel vocabulary. KYC invite still keys only on owner|decision_maker|trustee — attorneys are
-- typically not invited for IDV (docs/onboarding-legacy.md §3).
--
-- ADD VALUE IF NOT EXISTS matches 0104_service_type_discretionary; safe in a transaction on PG 12+
-- as long as the new value is not USED in the same transaction (it is not here).
ALTER TYPE crm.account_role ADD VALUE IF NOT EXISTS 'attorney';
ALTER TYPE crm.account_role ADD VALUE IF NOT EXISTS 'other';
