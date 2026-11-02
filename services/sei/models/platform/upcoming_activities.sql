/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.upcoming_activities, platform.tenancy, platform.accounts]
  tags: [sei, entity, conformed]
  description: >
    Expected future activity — pending trades, corporate actions and cash movements with
    their projected date and expected cash impact. The action-required surfaces read this.
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- Signed like every other SEI money column: EXPECTED_QUANTITY, EXPECTED_AMOUNT and the two
-- cash-impact figures each have their own debit/credit flag, so the raw columns cannot be
-- summed as delivered.
WITH latest AS (SELECT max(dt) AS dt FROM raw.upcoming_activities),
current AS (
    SELECT u.* FROM raw.upcoming_activities u JOIN latest ON u.dt = latest.dt
    QUALIFY row_number() OVER (
        PARTITION BY u."ACTIVITY_REF", u."ACCOUNT_NUMBER", u."TRD_INSTRUMENT_ID"
        ORDER BY u."PROJECTED_DATE" NULLS LAST
    ) = 1
)
SELECT
    -- The conformed spine (docs/platform-model.md §3), published alongside the tenancy
    -- columns rather than instead of them — see platform/accounts.sql for why the fan-out
    -- switch is a step of its own.
    concat_ws(':', u."ACCOUNT_NUMBER", u."ACTIVITY_REF", u."TRD_INSTRUMENT_ID") AS external_id,
    -- THE ORG COMES FROM THE ACCOUNT. platform.accounts resolves key/lineage once (see its
    -- header) and every account-grained relation selects it, so a fact and the account it is
    -- about cannot disagree about which node owns them. Recomputed here it had diverged, and
    -- one of the spellings of "unknown" was `[]` — which the fan-out reads as GLOBAL.
    acc.key,
    acc.lineage,
    NULL::timestamp                                 AS source_updated_at,
    u."ACCOUNT_NUMBER"                              AS account_external_id,
    u."PORTFOLIO_ID"                                AS portfolio_id,
    u."PORTFOLIO_LONG_NAME"                         AS portfolio_name,
    u."TRD_INSTRUMENT_ID"                           AS instrument_id,
    u."ACTIVITY_REF"                                AS activity_ref,
    u."ACTIVITY_TYPE"                               AS activity_type,
    u."ACTIVITY_DESCRIPTION"                        AS activity_description,
    u."ACTIVITY_STATUS"                             AS activity_status,
    u."ORDER_TYPE"                                  AS order_type,
    u."ORDER_SPECIFICATION"                         AS order_specification,
    u."TIME_IN_FORCE"                               AS time_in_force,
    u."CA_EVENT_TYPE"                               AS corporate_action_type,
    u."CA_EVENT_STATUS"                             AS corporate_action_status,
    u."TRADED_PENDING_SETTLEMENT_FLG"               AS is_traded_pending_settlement,
    u."PROJECTED_DATE"                              AS projected_on,
    u."ESTIMATED_POSTING_DATE"                      AS estimated_posting_on,
    u."ESTIMATED_SETTLEMENT_DATE"                   AS estimated_settlement_on,
    u."CREATED_DT"                                  AS created_on,
    (CASE WHEN u."EXPECTED_QTY_DR_CR_FLAG" = 'D' THEN -1 ELSE 1 END) * u."EXPECTED_QUANTITY"
                                                    AS expected_quantity,
    (CASE WHEN u."EXPECTED_AMOUNT_DR_CR_FLAG" = 'D' THEN -1 ELSE 1 END) * u."EXPECTED_AMOUNT"
                                                    AS expected_amount,
    u."EXPECTED_AMOUNT_CURR"                        AS expected_amount_currency,
    (CASE WHEN u."EXP_CASH_IMPACT_DR_CR_FLG" = 'D' THEN -1 ELSE 1 END) * u."EXPECTED_NET_CASH_IMPACT"
                                                    AS expected_net_cash_impact,
    u."EXPECTED_CASH_IMPACT_CURR"                   AS expected_cash_impact_currency,
    (CASE WHEN u."EXP_BASE_CASH_IMPCT_DR_CR_FLG" = 'D' THEN -1 ELSE 1 END) * u."EXP_CASH_IMPACT_BASE_EST"
                                                    AS expected_cash_impact_base,
    u."EXPECTED_PERCENTAGE"                         AS expected_percentage,
    u."RELATED_ORDER_REF"                           AS related_order_ref,
    u."INITIATOR_ACTIVITY_TYPE"                     AS initiator_activity_type,
    coalesce(t.firm_id, u."FIRM_ID")                AS firm_id,
    t.subfirm_id,
    coalesce(t.dsr_team_ids, [])                    AS dsr_team_ids,
    u.dt                                            AS as_of_date,
    to_json(u)                                      AS data
FROM current u
LEFT JOIN platform.tenancy t ON t.account_number = u."ACCOUNT_NUMBER"
LEFT JOIN platform.accounts acc ON acc.external_id = u."ACCOUNT_NUMBER"
