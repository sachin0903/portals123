/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.costs_charges]
  tags: [sei, entity, conformed]
  description: >
    MiFID ex-ante / ex-post cost and charge disclosures per instrument — entry and exit
    costs, ongoing charges, transaction and performance fees, estimated and actual.
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- Named for what it is: these are the instrument's OWN costs (the OCF/transaction-cost figures
-- a firm discloses), not the fees SEI charges an account. Account-level fees live in the
-- FeeComputation / FeePackage reports, which are separate and not yet modelled.
--
-- Every figure comes in an estimate/actual pair with its own effective date, kept as parallel
-- columns rather than unpivoted: an estimate and an actual are different assertions about the
-- same period, not two rows of the same series.
WITH latest AS (SELECT max(dt) AS dt FROM raw.costs_charges),
current AS (
    SELECT c.* FROM raw.costs_charges c JOIN latest ON c.dt = latest.dt
    QUALIFY row_number() OVER (PARTITION BY c."INSTRUMENT_ID", c."FIRM_ID"
                               ORDER BY c."ONGOING_CHARGE_ACTUAL_DT" DESC NULLS LAST) = 1
)
SELECT
    -- The conformed spine (docs/platform-model.md §3), published alongside the tenancy
    -- columns rather than instead of them — see platform/accounts.sql for why the fan-out
    -- switch is a step of its own.
    concat_ws(':', c."FIRM_ID", c."INSTRUMENT_ID")  AS external_id,
    c."FIRM_ID"                                     AS key,
    -- `nullif(…, [])` because an EMPTY lineage is the fan-out's GLOBAL sentinel: without it a
    -- row whose FIRM_ID is blank would project to EVERY tenant rather than to none. See
    -- platform/accounts.sql for the whole rule — only genuinely global reference data
    -- (platform.instruments) may publish `[]`.
    nullif(list_filter([c."FIRM_ID"], x -> x IS NOT NULL),
           []::VARCHAR[])                           AS lineage,
    NULL::timestamp                                 AS source_updated_at,
    c."INSTRUMENT_ID"                               AS instrument_id,
    c."ONE_OFF_ENTRY_COST"                          AS one_off_entry_cost,
    c."ONE_OFF_MAX_ENTRY_COST_ACQUIRED"             AS one_off_max_entry_cost,
    c."ONE_OFF_MAX_EXIT_COST"                       AS one_off_max_exit_cost,
    c."ONE_OFF_TYPICAL_EXIST_COST"                  AS one_off_typical_exit_cost,
    c."ONGOING_CHARGE_ESTIMATE"                     AS ongoing_charge_estimate,
    c."ONGOING_CHARGE_ACTUAL"                       AS ongoing_charge_actual,
    c."ONGOING_CHARGE_ESTIMATE_DT"                  AS ongoing_charge_estimate_on,
    c."ONGOING_CHARGE_ACTUAL_DT"                    AS ongoing_charge_actual_on,
    c."TRANSACTION_FEE_ESTIMATE"                    AS transaction_fee_estimate,
    c."TRANSACTION_FEE_ACTUAL"                      AS transaction_fee_actual,
    c."DISTRIBUTION_FEE_ESTIMATE"                   AS distribution_fee_estimate,
    c."DISTRIBUTION_FEE_ACTUAL"                     AS distribution_fee_actual,
    c."MANAGEMENT_FEE_ESTIMATE"                     AS management_fee_estimate,
    c."MANAGEMENT_FEE_ACTUAL"                       AS management_fee_actual,
    c."INCIDENTAL_COST_ESTIMATE"                    AS incidental_cost_estimate,
    c."INCIDENTAL_COST_ACTUAL"                      AS incidental_cost_actual,
    c."PERFORMANCE_FEE_CHARGED"                     AS performance_fee_charged,
    c."PERFORMANCE_FEE_INDEX"                       AS performance_fee_index,
    c."HURDLE_RATE"                                 AS hurdle_rate,
    c."HURDLE_RATE_FREQUENCY"                       AS hurdle_rate_frequency,
    c."HIGH_WATERMARK_FLAG"                         AS high_watermark,
    c."FIRM_ID"                                     AS firm_id,
    c.dt                                            AS as_of_date,
    to_json(c)                                      AS data
FROM current c
