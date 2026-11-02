/*
interlace:
  materialise: view
  depends_on: ref.reference_current
  tags: [sei, ref]
  description: >
    The remaining Reference dimensions the marts join to (transaction types, asset
    types, currencies, treatment/fee/activity codes, description codes), one row
    per code with its TYPE retained.
*/

-- The cherry-picked transformations reference these as separate tables
-- (transaction_types, asset_types, treatment_codes, fee_types, activity_types,
-- reference_codes, exchange_rates). They are all the same shape, so they are one
-- view here and the marts filter on `type` — 30k Reference rows do not justify
-- seven near-identical views, and a new SEI code type then needs no new model.
SELECT
    type,
    code,
    description,
    firm_id,
    qualifier_1,
    qualifier_2,
    qualifier_3,
    as_of_date
FROM ref.reference_current
WHERE type NOT IN ('FIRM_ID', 'SUBFIRM', 'DSR')
