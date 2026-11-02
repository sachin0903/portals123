/*
interlace:
  materialise: view
  depends_on: [ref.reference_current, ref.firms]
  tags: [sei, ref, fanout]
  description: >
    SEI DSR *types* — the category a DSR grant belongs to (IFA Firm, Client Service
    Team, SEI Custody, …). The discriminator that says which of an account's DSR
    grants identifies the advising firm, i.e. the tenant.
*/

-- Why this matters for tenant selection: an account carries SEVERAL DSR grants of
-- DIFFERENT types (~3.5 on average in the p04 drop), so a DSR team id on its own is
-- ambiguous. "IFA Firm" / "Adviser Firm" is the grant that names the advisory firm;
-- "SEI Custody", "Client Service Team", "Legal Entity" and "Service Type" are
-- operational segregation that every account in the firm shares.
--
-- The type vocabulary is PER FIRM and inconsistent across firms — 3102/3106 call it
-- "IFA Firm", 3110/3114 call it "Adviser Firm", and 3108/3112 have no firm-level type
-- at all. So there is no single global "this is the tenant type" rule; it has to be
-- resolved per firm (docs/sei.md §3).
SELECT
    t.code            AS dsr_type_id,
    t.description     AS dsr_type,
    t.firm_id,
    f.firm_name,
    -- Does this type identify an advisory firm (a candidate tenant) rather than an
    -- operational team? Naming is the only signal SEI gives us, so this is a hint for
    -- the operator choosing grants, never an automatic authorisation decision.
    (t.description ILIKE '%IFA Firm%' OR t.description ILIKE '%Adviser Firm%')
                      AS identifies_firm,
    t.as_of_date
FROM ref.reference_current t
LEFT JOIN ref.firms f ON f.firm_id = t.firm_id
WHERE t.type = 'DSR_TYPE_NAME'
