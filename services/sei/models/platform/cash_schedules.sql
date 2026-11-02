/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.rec_cash_activities, platform.tenancy, platform.accounts]
  tags: [sei, entity, conformed]
  description: >
    Recurring cash instructions — standing contributions and withdrawals with their
    recurrence rule, payer profile and next due date. The regular-movements surface
    on an account reads this.
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- The recurrence arrives as eighteen columns describing daily / weekly / monthly variants of
-- one rule. They are kept as-is rather than collapsed into an RRULE: the mapping is lossy in
-- both directions (SEI has a "day type" and a week-day resolution preference RRULE has no
-- word for), and inventing a normal form here would hide that from whoever has to honour it.
WITH latest AS (SELECT max(dt) AS dt FROM raw.rec_cash_activities),
current AS (
    SELECT r.* FROM raw.rec_cash_activities r JOIN latest ON r.dt = latest.dt
    QUALIFY row_number() OVER (
        PARTITION BY r."ACCOUNT_NUMBER", r."PROCESSING_SCHEDULE_ID", r."TRANSACTION_TYPE"
        ORDER BY r."NEXT_ACTIVITY_START_DATE" DESC NULLS LAST
    ) = 1
)
SELECT
    -- The conformed spine (docs/platform-model.md §3), published alongside the tenancy
    -- columns rather than instead of them — see platform/accounts.sql for why the fan-out
    -- switch is a step of its own.
    concat_ws(':', r."ACCOUNT_NUMBER", r."PROCESSING_SCHEDULE_ID", r."TRANSACTION_TYPE")
                                                    AS external_id,
    -- THE ORG COMES FROM THE ACCOUNT. platform.accounts resolves key/lineage once (see its
    -- header) and every account-grained relation selects it, so a fact and the account it is
    -- about cannot disagree about which node owns them. Recomputed here it had diverged, and
    -- one of the spellings of "unknown" was `[]` — which the fan-out reads as GLOBAL.
    acc.key,
    acc.lineage,
    NULL::timestamp                                 AS source_updated_at,
    r."ACCOUNT_NUMBER"                              AS account_external_id,
    r."PORTFOLIO_ID"                                AS portfolio_id,
    r."TRANSACTION_TYPE"                            AS transaction_type,
    r."PROCESSING_SCHEDULE_ID"                      AS schedule_id,
    r."RECURRENCE_FREQUENCY"                        AS frequency,
    r."RECURRANCE_SCHEDULE_START_DT"                AS starts_on,
    r."RECURRENCE_SCHEDULE_END_DT"                  AS ends_on,
    r."RECURRENCE_NO_END_DATE_FLG"                  AS is_open_ended,
    r."RECURRENCE_END_AFTER_X_OCCURRENCES"          AS ends_after_occurrences,
    r."NEXT_ACTIVITY_START_DATE"                    AS next_due_on,
    r."NEXT_ACTIVITY_ID"                            AS next_activity_id,
    r."GROSS_AMOUNT"                                AS gross_amount,
    r."NET_AMOUNT"                                  AS net_amount,
    r."CURRENCY_CODE"                               AS currency,
    r."AMOUNT_SPECIFICATION_TYPE"                   AS amount_specification_type,
    r."PAY_TO_METHOD"                               AS pay_to_method,
    r."PAY_TO_PROFILE_ID"                           AS pay_to_profile_id,
    r."PAYER_NAME"                                  AS payer_name,
    r."PAYER_PROFILE_ID"                            AS payer_profile_id,
    r."PAYER_ACCOUNT_NUMBER"                        AS payer_account_number,
    r."PAYER_BANK_ID"                               AS payer_bank_id,
    list_filter([r."PAYER_ADDRESS_LINE_1", r."PAYER_ADDRESS_LINE_2", r."PAYER_ADDRESS_LINE_3"],
                x -> x IS NOT NULL AND x <> '')     AS payer_address_lines,
    r."PAYER_CITY"                                  AS payer_city,
    r."PAYER_POSTAL_CODE"                           AS payer_postcode,
    r."PAYER_COUNTRY"                               AS payer_country,
    r."INVEST_CASH_FLG"                             AS invests_cash,
    r."INVEST_CASH_METHOD"                          AS invest_cash_method,
    r."RAISE_CASH_METHOD"                           AS raise_cash_method,
    r."TREATMENT_CODE"                              AS treatment_code,
    r."EXPLANATION"                                 AS explanation,
    coalesce(t.firm_id, r."FIRM_ID")                AS firm_id,
    t.subfirm_id,
    coalesce(t.dsr_team_ids, [])                    AS dsr_team_ids,
    r.dt                                            AS as_of_date,
    to_json(r)                                      AS data
FROM current r
LEFT JOIN platform.tenancy t ON t.account_number = r."ACCOUNT_NUMBER"
LEFT JOIN platform.accounts acc ON acc.external_id = r."ACCOUNT_NUMBER"
