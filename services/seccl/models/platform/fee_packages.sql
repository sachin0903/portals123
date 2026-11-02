/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: raw.fee_structures
  tags: [seccl, entity, conformed]
  description: >
    Priced fee structures, conformed — the firm's platform and adviser charges published as
    the fixed | percentage | tiered rule shape platform.fee_packages stores (0042).
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: name
    - not_null: firm_key
*/

-- THE RULE IS DERIVED, NOT COPIED. Seccl publishes one shape for all three kinds of charge —
-- a `tiers` array of {fromAmount, toAmount, rate, amount} — and which kind it is has to be read
-- out of it:
--
--   one tier, `amount`, no rate          -> fixed       {amount}
--   one tier, `rate`, no toAmount        -> percentage  {rate}
--   anything else carrying rates         -> tiered      {tiers: [{from, to, rate}]}
--
-- A structure that matches none of those (no tiers at all, or a multi-tier structure where some
-- tier carries no rate) publishes fee_type NULL. That is deliberate and mirrors the transaction
-- taxonomy: the materialiser counts an unmapped fee as a visible reject, and a reject is
-- recoverable where a fee filed under the wrong shape silently misprices an invoice.
--
-- RATES ARE FRACTIONS. Seccl prices a platform charge at `0.0035`, which is 0.35% — the same
-- convention `allocations`/`cash_allocation` use (verified against staging), and the one
-- app/materialise.py::_allocation_lines already documents. platform.fee_packages stores 0-100
-- with a CHECK, so the fraction is scaled here. Storing it unscaled would pass the check and
-- then charge a hundredth of the fee. The cast to DECIMAL before scaling is not decoration:
-- `0.0035::double * 100` is 0.35000000000000003.
--
-- BAND BOUNDARIES ARE NORMALISED CONTIGUOUS. Seccl expresses the next band as the next penny
-- (`[0, 10000]` then `[10000.01, open]`); the canonical rule requires each band to start where
-- the previous one ended, and app/catalogue.py::validate_fee_rule rejects a gap outright — a gap
-- charges nothing inside it. So each tier's `from` is taken as the PREVIOUS tier's `to`, which is
-- the same pricing under a half-open convention. Seccl's own boundaries survive in `data`.
--
-- `rule` IS JSON, NOT A STRUCT. The three shapes have three different field sets, and a CASE
-- whose branches are structs of different types does not bind at all.
--
-- NOT PUBLISHED: minimum_amount, maximum_amount, effective_from/to, vatable. 0042 gave
-- fee_packages columns for them because Seccl's tiered ADVISER CHARGE (the per-account
-- `adviserTieredCharges` block) carries them — but this endpoint's own documented payload is
-- {id, type, name, tiers, excludedWrapperTypes} and nothing more, and staging has no fee
-- structure to read a live shape from (verified 04 Aug 2026: meta.count 0). Binding those
-- columns to guessed field names would publish NULL while looking checked; they stay for the
-- admin catalogue to set until a real payload shows otherwise.
WITH source AS (
    SELECT
        cast(f.id AS varchar)                                     AS external_id,
        cast(f.firm_id AS varchar)                                AS firm_key,
        f.type                                                    AS fee_category_raw,
        f.name,
        coalesce(try_cast(f.tiers AS json[]), [])                  AS tier_list,
        -- The wrappers this structure does NOT apply to. Kept as the exclusion Seccl states
        -- rather than inverted here: the complement needs the canonical wrapper set, which the
        -- materialiser owns (PRODUCT_TYPES + its PENSION -> SIPP alias) and this layer does not.
        coalesce(try_cast(f.excluded_wrapper_types AS varchar[]), []) AS excluded_products,
        to_json(f)                                                AS data
    FROM raw.fee_structures AS f
),

shaped AS (
    SELECT
        src.*,
        len(src.tier_list)                                          AS tier_count,
        try_cast(src.tier_list[1] ->> 'amount' AS decimal(20, 4))   AS first_amount,
        try_cast(src.tier_list[1] ->> 'rate' AS decimal(12, 8))     AS first_rate,
        try_cast(src.tier_list[1] ->> 'toAmount' AS decimal(20, 4)) AS first_to,
        -- Every tier prices by rate. A multi-tier structure with an amount-only band cannot be
        -- expressed as a canonical tiered rule, so it must not be published as one.
        len(list_filter(src.tier_list, x -> (x ->> 'rate') IS NULL)) = 0 AS every_tier_has_a_rate,
        -- `from` is the previous band's ceiling; the first band starts at zero.
        [
            {
                'from': CASE
                    WHEN i = 1 THEN 0::decimal(20, 4)
                    ELSE try_cast(src.tier_list[i - 1] ->> 'toAmount' AS decimal(20, 4))
                END,
                'to': try_cast(src.tier_list[i] ->> 'toAmount' AS decimal(20, 4)),
                'rate': try_cast(src.tier_list[i] ->> 'rate' AS decimal(12, 8)) * 100
            }
            FOR i IN range(1, len(src.tier_list) + 1)
        ]                                                           AS bands
    FROM source AS src
),

classified AS (
    SELECT
        shaped.*,
        CASE
            WHEN tier_count = 0 THEN NULL
            WHEN tier_count = 1 AND first_rate IS NULL AND first_amount IS NOT NULL THEN 'fixed'
            WHEN tier_count = 1 AND first_rate IS NOT NULL AND first_to IS NULL THEN 'percentage'
            WHEN every_tier_has_a_rate THEN 'tiered'
        END                                                       AS fee_type
    FROM shaped
)

SELECT
    c.external_id,
    -- FIRM-WIDE, so the empty lineage that admits a row to every tenant -- the same treatment
    -- instruments and transfer_providers get, and NULL key for the same reason: this row is
    -- not owned by one node.
    --
    -- The alternative -- scoping it to the firm ROOT's org -- was tried and is wrong. `lineage`
    -- containment (fanout._conformed_predicate) tests ANCESTRY: a client at node 41 publishes
    -- [0, 9, 41] and matches a tenant holding 9. A row owned by the root publishes [0] and
    -- matches only a tenant holding 0 -- so every tenant enabled on a sub-node, which is the
    -- normal case, would silently receive none of its own firm's pricing. (Verified on dev, where
    -- the tenant is enabled on node 1: `platform.third_party_providers` all carry the firm root's
    -- [0] and the fan-out projects 0 of the firm's 7 to it. Same shape of bug, still open.)
    --
    -- THE PREMISE this rests on: one Seccl firm per catalogue. `SECCL_FIRM_ID` is single-valued
    -- and a second firm means a second `.env`, a second metadata schema and a second control
    -- plane (interlace.yaml documents exactly that for staging vs production), so "every tenant
    -- in this catalogue" and "every tenant of this firm" are the same set. `firm_key` is
    -- published so a row still says which firm priced it, and so that premise is checkable
    -- rather than assumed. If a catalogue is ever made to hold two firms, this column is what
    -- the predicate would have to start narrowing on -- and transfer_providers, already global,
    -- would need the same treatment.
    NULL::varchar                                                 AS key,
    []::varchar[]                                                 AS lineage,
    c.firm_key,
    c.name,
    -- 'Platform Fee' -> 'platform', 'Adviser Fee' -> 'adviser'. The admin catalogue's category is
    -- free text and its own default is 'platform', so this matches what a hand-added package
    -- looks like rather than introducing a second spelling for the same thing.
    nullif(lower(trim(replace(c.fee_category_raw, 'Fee', ''))), '') AS category,
    c.fee_category_raw,
    c.fee_type,
    -- A charge structure is what the client pays while invested, as opposed to an initial fee on
    -- the way in -- Seccl bills those as transactions (POST /portfoliotransactiongroup), not as
    -- structures, so nothing in this register is 'initial'.
    'ongoing'                                                     AS frequency,
    CASE c.fee_type
        WHEN 'fixed' THEN to_json({'amount': c.first_amount})
        WHEN 'percentage' THEN to_json({'rate': c.first_rate * 100})
        WHEN 'tiered' THEN to_json({'tiers': c.bands})
    END                                                           AS rule,
    -- A rate prices a proportion of the holding; a flat amount does not.
    CASE WHEN c.fee_type = 'fixed' THEN 'flat'
         WHEN c.fee_type IN ('percentage', 'tiered') THEN 'aum'
    END                                                           AS charge_basis,
    -- Both platform and adviser charges are deducted from the client's account.
    'client'                                                      AS payer,
    'GBP'                                                         AS currency,
    c.excluded_products,
    c.tier_count,
    -- No audit block on this payload (see the note above), so the extract date is the only
    -- assertion time available.
    NULL::timestamp                                               AS source_updated_at,
    current_date                                                  AS as_of_date,
    c.data
FROM classified AS c
