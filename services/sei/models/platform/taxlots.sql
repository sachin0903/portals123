/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.taxlots]
  tags: [sei, entity, conformed]
  description: >
    Tax lots — the acquisition-level detail behind a position: cost basis, acquisition
    date and method, and realised gain/loss inputs. The CGT layer reads this.
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- Org is the FIRM only. Taxlots carries FIRM_ID and CUSTODIAN_ACCOUNT_NUMBER but not the SEI
-- ACCOUNT_NUMBER the tenancy spine is keyed on, so the subfirm/DSR narrowing cannot be
-- resolved here without a POSK_ID -> account bridge that does not exist yet. Firm-level is
-- honest and still scopes the row; narrowing it further would need that bridge built first.
WITH latest AS (SELECT max(dt) AS dt FROM raw.taxlots),
current AS (
    SELECT t.* FROM raw.taxlots t JOIN latest ON t.dt = latest.dt
    -- A taxlot id repeats across portfolios and instruments in this feed, so the declared
    -- key is the triple; the pick keeps it deterministic.
    QUALIFY row_number() OVER (
        PARTITION BY t."TAXLOT_ID", t."PORTFOLIO_ID", t."INSTRUMENT_ID"
        ORDER BY t."ACQUISITION_DATE" DESC NULLS LAST, t."POSK_ID"
    ) = 1
)
SELECT
    -- The conformed spine (docs/platform-model.md §3), published alongside the tenancy
    -- columns rather than instead of them — see platform/accounts.sql for why the fan-out
    -- switch is a step of its own.
    concat_ws(':', t."TAXLOT_ID", t."PORTFOLIO_ID", t."INSTRUMENT_ID") AS external_id,
    t."FIRM_ID"                                     AS key,
    -- `nullif(…, [])` because an EMPTY lineage is the fan-out's GLOBAL sentinel: without it a
    -- row whose FIRM_ID is blank would project to EVERY tenant rather than to none. See
    -- platform/accounts.sql for the whole rule — only genuinely global reference data
    -- (platform.instruments) may publish `[]`.
    nullif(list_filter([t."FIRM_ID"], x -> x IS NOT NULL),
           []::VARCHAR[])                           AS lineage,
    NULL::timestamp                                 AS source_updated_at,
    t."TAXLOT_ID"                                   AS taxlot_id,
    t."PORTFOLIO_ID"                                AS portfolio_id,
    t."INSTRUMENT_ID"                               AS instrument_id,
    t."CUSTODIAN_ACCOUNT_NUMBER"                    AS custodian_account_number,
    t."TAXLOT_STATUS_DESC"                          AS status,
    t."TAXLOT_TYPE_DESC"                            AS taxlot_type,
    t."POSITION_TYPE_DESC"                          AS position_type,
    t."ACQUISITION_METHOD_DESC"                     AS acquisition_method,
    t."TRANSACTION_DATE"                            AS transaction_date,
    t."TRADE_DATE"                                  AS trade_date,
    t."ACQUISITION_DATE"                            AS acquired_on,
    t."SETTLEMENT_DATE"                             AS settled_on,
    t."QUANTITY_HELD"                               AS quantity,
    t."TAXLOT_QTY"                                  AS taxlot_quantity,
    t."MARKET_VALUE"                                AS market_value,
    t."ORIGINAL_COST_BASE"                          AS original_cost_base,
    t."ORIGINAL_COST_LOCAL"                         AS original_cost_local,
    t."INV_COST_BASE"                               AS cost_base,
    t."INV_PROCEEDS_BASE"                           AS proceeds_base,
    t."INV_REALIZED_GL_BASE"                        AS realised_gain_base,
    t."FAIR_MKT_VALUE_BASE"                         AS fair_market_value_base,
    t."CURRENT_FACE_AMOUNT"                         AS current_face_amount,
    t."LOCAL_CURRENCY"                              AS local_currency,
    t."FX_RATE"                                     AS fx_rate,
    t."INSTRUMENT_IS_CURRENCY_FLAG"                 AS is_currency,
    t."LIABILITY_INSTRUMENT_FLAG"                   AS is_liability,
    t."TRANSACTION_ID"                              AS transaction_id,
    t."ACQ_TRANSACTION_ID"                          AS acquisition_transaction_id,
    t."FIRM_ID"                                     AS firm_id,
    t.dt                                            AS as_of_date,
    to_json(t)                                      AS data
FROM current t
