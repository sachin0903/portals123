/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.fee_package_rule, ref.firms]
  tags: [sei, entity, conformed]
  description: >
    SEI periodic fee packages (the firm's ongoing adviser / platform / DFM charges), conformed as
    the fixed | percentage | tiered rule shape platform.fee_packages stores (0042). The key is the
    SEI feePackageProfileId (external_id) — the id FeesApi.assign_package_to_account takes. Real-time
    (dealing) fees and one-time ad-hoc fee TYPES are separate SEI axes and are NOT in this model.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: name
    - not_null: firm_key
*/

-- ONE ROW PER PROFILE. SEI's FeePackageRule is per-RULE and the nightly drop repeats each rule, so a
-- profile (feePackageProfileId) shows as several identical rows; we DISTINCT them to the rule. A
-- profile that still carries more than one DISTINCT rule cannot be expressed as one canonical package
-- rule, so it publishes fee_type NULL — a visible reject (the materialiser's requires() rejects a
-- null fee_type/rule), the same recoverable treatment the Seccl model gives an unmappable structure.
--
-- THE RATE IS A SEI MARKUP STRING, PARSED NOT COPIED. SEI encodes the rate three ways:
--     Flat Percentage   <rt>.1%</rt>                                  -> percentage {rate}
--     Flat Amount       <rt>712.8</rt>                                -> fixed      {amount}
--     Tiered Percentage <slb><lb>0</lb><hb>600000</hb><rt>.26%</rt></slb>...  -> tiered {tiers}
-- The `%` is the discriminator: `<rt>N%</rt>` is a percentage, `<rt>N</rt>` is a currency amount.
--
-- RATES ARE ALREADY PERCENTAGES, NOT FRACTIONS (unlike Seccl, which prices 0.35% as 0.0035 and is
-- scaled x100 in its model). SEI's `.1%` is 0.1, which is exactly what platform.fee_packages stores
-- under its 0-100 CHECK. So there is NO scaling here; adding one would charge a hundred times the fee.
--
-- BANDS ARE ALREADY CONTIGUOUS. SEI publishes `[0,600000]` then `[600000,1200000]` — each band
-- already starts where the previous ended, the half-open shape app/catalogue.py::validate_fee_rule
-- wants, so the bounds are taken as published (the last band's empty <hb></hb> is the open top = NULL).
--
-- firm_id IS FOR THE SLICE PREDICATE, NOT THE BOOK ROW. book_sink.predicate.sei narrows fee_packages
-- to `firm_id IN (tenant config firm_id)`; the column is read by that filter and dropped by staging
-- (platform.fee_packages has no firm column — tenant_id is stamped from the TenantSlice). Seccl is
-- firm-per-catalogue and uses an empty lineage instead; SEI holds many firms, so it must narrow here.
WITH active AS (
    SELECT
        r.FEE_PACKAGE_PROFILE_ID                                     AS external_id,
        CAST(f.firm_id AS varchar)                                  AS firm_id,
        r.FEE_PACKAGE_NAME                                          AS name,
        r.FEE_TYPE_DESCRIPTION                                      AS fee_category_raw,
        r.FEE_PACKAGE_RULE_RATE_TYPE                                AS rate_type,
        r.FEE_PACKAGE_RULE_BASIS                                    AS basis,
        nullif(r.FEE_RULE_CURRENCY, '')                            AS currency,
        r.RATE                                                      AS rate_raw
    FROM raw.fee_package_rule AS r
    JOIN ref.firms AS f ON f.firm_name = r.RESIDING_ORGANIZATION
    WHERE r.FEE_PACKAGE_TYPE = 'Periodic'
      AND coalesce(r.FEE_PACKAGE_STATUS, '1') = '1'
      AND r.FEE_PACKAGE_PROFILE_ID IS NOT NULL
),

-- One row per DISTINCT RULE in a profile. The rule is (rate_type, basis, rate_raw); name/currency
-- vary row-to-row in the drop (trailing text, casing) and must NOT split a profile, so they are a
-- representative min(), not part of the grouping — otherwise a one-rule profile looks ambiguous.
rules AS (
    SELECT
        external_id,
        rate_type,
        basis,
        rate_raw,
        min(firm_id)          AS firm_id,
        min(name)             AS name,
        min(fee_category_raw) AS fee_category_raw,
        min(currency)         AS currency
    FROM active
    GROUP BY external_id, rate_type, basis, rate_raw
),

counted AS (
    SELECT r.*, count(*) OVER (PARTITION BY external_id) AS rules_for_profile
    FROM rules AS r
),

shaped AS (
    SELECT
        c.*,
        CASE
            WHEN rules_for_profile > 1 THEN NULL          -- ambiguous package -> visible reject
            WHEN rate_type = 'Flat Percentage' THEN 'percentage'
            WHEN rate_type = 'Tiered Percentage' THEN 'tiered'
            WHEN rate_type = 'Flat Amount' THEN 'fixed'
        END                                                         AS fee_type,
        try_cast(regexp_extract(rate_raw, '<rt>([0-9.]+)%', 1) AS decimal(12, 6))  AS flat_rate,
        try_cast(regexp_extract(rate_raw, '<rt>([0-9.]+)</rt>', 1) AS decimal(20, 4)) AS flat_amount,
        [
            {
                'from': try_cast(regexp_extract(b, '<lb>([0-9.]*)</lb>', 1) AS decimal(20, 2)),
                'to': try_cast(nullif(regexp_extract(b, '<hb>([0-9.]*)</hb>', 1), '') AS decimal(20, 2)),
                'rate': try_cast(regexp_extract(b, '<rt>([0-9.]+)%', 1) AS decimal(12, 6))
            }
            FOR b IN regexp_extract_all(rate_raw, '<slb>.*?</slb>')
        ]                                                           AS bands
    FROM counted AS c
)

SELECT
    s.external_id,
    -- Firm-scoped: the predicate narrows on firm_id. key/lineage carry the firm axis as a NON-empty
    -- [firm_id] — never the empty [] that is the fan-out's GLOBAL sentinel (see the lineage-sentinel
    -- test and models.sql). The predicate reads firm_id below, not lineage; this just satisfies the
    -- "non-global publishes a real lineage" rule.
    s.firm_id                                                       AS key,
    [s.firm_id]                                                     AS lineage,
    s.firm_id                                                       AS firm_key,
    s.firm_id,                                                      -- read by the SEI slice predicate
    s.name,
    -- 'Ongoing Adviser Fee' -> 'ongoing adviser', 'Platform Fee' -> 'platform'. Matches the Seccl
    -- model's free-text category derived from the fee-type description.
    nullif(lower(trim(replace(s.fee_category_raw, 'Fee', ''))), '') AS category,
    s.fee_category_raw,
    s.fee_type,
    'ongoing'                                                       AS frequency,
    CASE s.fee_type
        WHEN 'fixed' THEN to_json({'amount': s.flat_amount})
        WHEN 'percentage' THEN to_json({'rate': s.flat_rate})
        WHEN 'tiered' THEN to_json({'tiers': s.bands})
    END                                                             AS rule,
    CASE
        WHEN s.fee_type = 'fixed' THEN 'flat'
        WHEN s.fee_type IN ('percentage', 'tiered') THEN 'aum'
    END                                                             AS charge_basis,
    'client'                                                        AS payer,
    coalesce(s.currency, 'GBP')                                     AS currency,
    []::varchar[]                                                   AS excluded_products,
    CASE WHEN s.fee_type = 'tiered' THEN len(s.bands) ELSE 1 END    AS tier_count,
    NULL::timestamp                                                 AS source_updated_at,
    current_date                                                    AS as_of_date,
    to_json(
        {
            'profile_id': s.external_id,
            'rate_type': s.rate_type,
            'basis': s.basis,
            'rate_raw': s.rate_raw,
            'fee_type_description': s.fee_category_raw
        }
    )                                                               AS data
FROM shaped AS s
-- Exactly one row per profile. A profile with >1 distinct rule already carries fee_type NULL (the
-- visible reject); this keeps a single reject row so external_id stays unique for the merge key.
QUALIFY row_number() OVER (PARTITION BY s.external_id ORDER BY s.rate_raw) = 1
