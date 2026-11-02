/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.paid_to, platform.tenancy]
  tags: [sei, entity, conformed]
  description: >
    Pay-to profiles from the PaidTo report, one row per account and profile.
    An input to platform.accounts, so it reads tenancy itself rather than the
    account it is about to help build.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: profile_id
*/

-- PaidTo is where SEI already holds a standing permission to send money out of an
-- account. One row per (account, profile) on the latest file. The book does not have
-- a profile table: `entry` is the object `platform.accounts.bank_accounts` stores,
-- and that model aggregates it. This one cannot read `platform.accounts` for
-- key/lineage, because accounts reads this.
--
-- PAY_TO_METHOD and ROUTING_NUMBER_TYPE are Reference codes (PAY_TO_METHOD,
-- ROUTING_NUMBER_TYPE). Code 8 (SEPA) has no book method, so the method is left
-- unset rather than given a word the book does not use. An ad hoc profile is not a
-- reusable destination: it stays on this model and is left out of the account array.
-- Correspondence and intermediate-bank columns stay on `data`. Payers are not in
-- this file.
WITH latest AS (
    SELECT max(dt) AS dt FROM raw.paid_to
),
current AS (
    SELECT p.*
    FROM raw.paid_to p
    JOIN latest ON p.dt = latest.dt
    WHERE nullif(trim(p."PROFILE_ID"), '') IS NOT NULL
    QUALIFY row_number() OVER (
        PARTITION BY p."ACCOUNT_NUMBER", p."PROFILE_ID"
        ORDER BY p._row
    ) = 1
),
shaped AS (
    SELECT
        p."ACCOUNT_NUMBER"                                          AS account_external_id,
        nullif(trim(p."PROFILE_ID"), '')                            AS profile_id,
        nullif(trim(p."PAY_TO_RECIPIENT_NAME"), '')                 AS name,
        nullif(trim(p."RECEIVING_BANK_NAME"), '')                   AS bank_name,
        nullif(trim(p."PAY_TO_METHOD"), '')                         AS method_raw,
        CASE nullif(trim(p."PAY_TO_METHOD"), '')
            WHEN '1' THEN 'direct_debit'
            WHEN '2' THEN 'transfer'
            WHEN '3' THEN 'cheque'
            WHEN '5' THEN 'bacs'
            WHEN '6' THEN 'chaps'
            WHEN '7' THEN 'swift'
            WHEN '9' THEN 'transfer'
            WHEN '10' THEN 'faster_payment'
        END                                                         AS method,
        CASE
            WHEN nullif(trim(p."ROUTING_NUMBER_TYPE"), '') = '5'
             AND length(regexp_replace(
                    coalesce(p."ROUTING_NUMBER", ''), '[^0-9]', '', 'g')) = 6
            THEN
                substr(regexp_replace(p."ROUTING_NUMBER", '[^0-9]', '', 'g'), 1, 2)
                || '-' ||
                substr(regexp_replace(p."ROUTING_NUMBER", '[^0-9]', '', 'g'), 3, 2)
                || '-' ||
                substr(regexp_replace(p."ROUTING_NUMBER", '[^0-9]', '', 'g'), 5, 2)
        END                                                         AS sort_code,
        CASE
            WHEN nullif(trim(p."ROUTING_NUMBER_TYPE"), '') = '2'
            THEN nullif(regexp_replace(
                upper(trim(coalesce(p."ROUTING_NUMBER", ''))), '[^A-Z0-9]', '', 'g'), '')
        END                                                         AS bic,
        nullif(regexp_replace(
            upper(trim(coalesce(p."IBAN", ''))), '[^A-Z0-9]', '', 'g'), '') AS iban,
        regexp_replace(coalesce(p."DESTINATION_ACCOUNT", ''), '[^0-9]', '', 'g')
                                                                    AS account_digits,
        coalesce(p."AD_HOC_PAY_TO_FLAG", false)                     AS is_ad_hoc,
        p."FIRM_ID"                                                 AS firm_id_raw,
        p.dt                                                        AS as_of_date,
        to_json(p)                                                  AS data,
        t.firm_id,
        t.subfirm_id,
        t.dsr_team_ids
    FROM current p
    LEFT JOIN platform.tenancy t ON t.account_number = p."ACCOUNT_NUMBER"
)
SELECT
    concat_ws(':', account_external_id, profile_id)            AS external_id,
    coalesce(subfirm_id, firm_id, firm_id_raw)                  AS key,
    nullif(list_distinct(list_filter(
        list_concat([coalesce(firm_id, firm_id_raw), subfirm_id],
                    coalesce(dsr_team_ids, [])),
        x -> x IS NOT NULL
    )), []::VARCHAR[])                                          AS lineage,
    as_of_date::timestamp                                       AS source_updated_at,
    data,
    account_external_id,
    profile_id,
    name,
    bank_name,
    method,
    method_raw,
    sort_code,
    CASE
        WHEN sort_code IS NULL THEN NULL
        WHEN length(account_digits) = 8 THEN account_digits
        WHEN length(account_digits) = 7 THEN lpad(account_digits, 8, '0')
        WHEN length(account_digits) > 0 THEN account_digits
    END                                                         AS account_number,
    iban,
    bic,
    is_ad_hoc,
    firm_id,
    subfirm_id,
    (
        '{' || concat_ws(',',
            '"direction":"pay_to"',
            '"provider":"sei"',
            '"provider_ref":' || to_json(profile_id),
            CASE WHEN name IS NOT NULL THEN '"name":' || to_json(name) END,
            CASE WHEN bank_name IS NOT NULL THEN '"bank":' || to_json(bank_name) END,
            CASE WHEN sort_code IS NOT NULL THEN '"sort_code":' || to_json(sort_code) END,
            CASE
                WHEN sort_code IS NOT NULL AND length(account_digits) = 8
                    THEN '"account_number":' || to_json(account_digits)
                WHEN sort_code IS NOT NULL AND length(account_digits) = 7
                    THEN '"account_number":' || to_json(lpad(account_digits, 8, '0'))
                WHEN sort_code IS NOT NULL AND length(account_digits) > 0
                    THEN '"account_number":' || to_json(account_digits)
            END,
            CASE WHEN iban IS NOT NULL THEN '"iban":' || to_json(iban) END,
            CASE WHEN bic IS NOT NULL THEN '"bic":' || to_json(bic) END,
            CASE WHEN method IS NOT NULL THEN '"method":' || to_json(method) END
        ) || '}'
    )::JSON                                                     AS entry
FROM shaped
