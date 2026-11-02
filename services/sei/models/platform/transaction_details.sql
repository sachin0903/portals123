/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.transaction_detail]
  tags: [sei, entity, conformed]
  description: >
    Transaction components — the fee, tax and treatment lines that make up a transaction's
    net amount. platform.transactions carries the header totals; this is the breakdown.
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- Signed the same way as the header: every amount is unsigned with the direction in the
-- DEBIT_CREDIT_FLAG_n immediately after it (TRANSACTION_AMT -> _1, TRANSACTION_QTY -> _2,
-- TRANSACTION_AMT_BASE -> _3, ORIGINAL_FEE_AMT -> _4). Summing the raw column adds debits to
-- credits and is wrong by construction.
--
-- Org is the FIRM: this report carries ACCOUNT_ID, which is not the ACCOUNT_NUMBER the
-- tenancy spine keys on, so the account-level narrowing cannot be resolved here.
WITH latest AS (SELECT max(dt) AS dt FROM raw.transaction_detail),
current AS (
    SELECT d.* FROM raw.transaction_detail d JOIN latest ON d.dt = latest.dt
    QUALIFY row_number() OVER (PARTITION BY d."TRANSACTION_DETAIL_ID" ORDER BY d."TRANSACTION_ID") = 1
)
SELECT
    -- The conformed spine (docs/platform-model.md §3), published alongside the tenancy
    -- columns rather than instead of them — see platform/accounts.sql for why the fan-out
    -- switch is a step of its own.
    d."TRANSACTION_DETAIL_ID"                       AS external_id,
    d."FIRM_ID"                                     AS key,
    -- `nullif(…, [])` because an EMPTY lineage is the fan-out's GLOBAL sentinel: without it a
    -- row whose FIRM_ID is blank would project to EVERY tenant rather than to none. See
    -- platform/accounts.sql for the whole rule — only genuinely global reference data
    -- (platform.instruments) may publish `[]`.
    nullif(list_filter([d."FIRM_ID"], x -> x IS NOT NULL),
           []::VARCHAR[])                           AS lineage,
    NULL::timestamp                                 AS source_updated_at,
    d."TRANSACTION_DETAIL_ID"                       AS transaction_detail_id,
    d."TRANSACTION_ID"                              AS transaction_id,
    d."TRANSACTION_TYPE"                            AS transaction_type,
    d."ACCOUNT_ID"                                  AS account_id,
    d."PORTFOLIO_ID"                                AS portfolio_id,
    d."COMPONENT_TYPE"                              AS component_type,
    d."FEE_TYPE"                                    AS fee_type,
    d."TAX_TYPE"                                    AS tax_type,
    d."TREATMENT_CODE"                              AS treatment_code,
    d."TRD_INSTRUMENT_ID"                           AS instrument_id,
    (CASE WHEN d."DEBIT_CREDIT_FLAG_1" = 'D' THEN -1 ELSE 1 END) * d."TRANSACTION_AMT"
                                                    AS amount,
    (CASE WHEN d."DEBIT_CREDIT_FLAG_2" = 'D' THEN -1 ELSE 1 END) * d."TRANSACTION_QTY"
                                                    AS quantity,
    (CASE WHEN d."DEBIT_CREDIT_FLAG_3" = 'D' THEN -1 ELSE 1 END) * d."TRANSACTION_AMT_BASE"
                                                    AS base_amount,
    (CASE WHEN d."DEBIT_CREDIT_FLAG_4" = 'D' THEN -1 ELSE 1 END) * d."ORIGINAL_FEE_AMT"
                                                    AS original_fee_amount,
    d."FEE_BASIS_NAME"                              AS fee_basis_name,
    d."FEE_BASIS_VALUE"                             AS fee_basis_value,
    d."OVERRIDE_FEE_NAME"                           AS override_fee_name,
    d."OVERRIDE_FEE_NOTES"                          AS override_fee_notes,
    d."NATIONAL_WITHHOLDING_TAX_RT"                 AS national_withholding_tax_rate,
    d."REGIONAL_WITHHOLDING_TAX_RT"                 AS regional_withholding_tax_rate,
    d."ADJUSTMENT_TYPE"                             AS adjustment_type,
    d."AVAILABILITY_DATE"                           AS available_on,
    d."FIRM_ID"                                     AS firm_id,
    d.dt                                            AS as_of_date,
    to_json(d)                                      AS data
FROM current d
