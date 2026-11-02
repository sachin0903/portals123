/*
interlace:
  materialise: virtual
  strategy: replace
  key: account_external_id
  depends_on: [raw.account_details, raw.accounts, raw.models]
  tags: [seccl, derived]
  description: >
    An account's own terms — what it is charged and what money moves on it by standing
    arrangement — shaped into the two lists the book already has columns for.
  checks:
    - not_null: account_external_id
    - unique: account_external_id
*/

-- ── Why this is its own model and not part of `platform/accounts.sql` ────────────────────────────
--
-- That model reads `raw.accounts` ALONE, deliberately: `/account/list` carries everything it needs, and
-- depending on the 376-call per-account fan-out made it unbuildable whenever that pull had not run —
-- which is how the first end-to-end attempt failed. The terms below only exist on the fan-out
-- (`/account/{firm}/{id}`), so they live here and the conformed model LEFT JOINs them. A fan-out that
-- returned nothing yields NULL terms and everything still builds; only a genuine API fault (which
-- `raw.account_details` raises on) stops the rebuild, and stopping is right there.
--
-- ── THREE CHARGES, THREE SOURCES, and they are not interchangeable ──────────────────────────────
--
--   * the ADVISER charge is the account's own, and it is EITHER a flat rate (`adviserCharge`, a
--     fraction — 0.01 is 1%) OR a tiered scale (`adviserTieredCharges.tiers`). Never both: v1's fee
--     model reads the flat one when it is present and falls back to the tiers, and that precedence is
--     preserved here because it is what the firm's own pricing means.
--   * the DFM charge is a property of the MODEL the account is managed to, so it comes from
--     `raw.models` through the account's `assetAllocationId`. An advisory account has none, which is
--     not a charge of zero.
--   * the PLATFORM charge is deliberately NOT here. Seccl's firm-level fee register is already
--     conformed as `platform.fee_packages` (off `/v1/fee-structures`), which is the priced, tiered,
--     wrapper-scoped source; the v1 pipeline read a `fees` array off the NODE instead, and this port
--     will not add a second answer to "what does the platform charge" that could disagree with the
--     register the firm actually edits.
--
-- THE TIER KEYS DIFFER BY SOURCE, which is the trap in this file. A platform tier is `{from, to}` and
-- an adviser tier is `{fromAmount, toAmount}` — the same concept under two spellings in one vendor's
-- API. Normalised to `amount_from`/`amount_to` here so nothing downstream has to know, and stated
-- because reading the wrong pair yields NULL bounds, which price the whole balance at the first tier's
-- rate and look entirely plausible.
--
-- ── `recurringPayments` is ONE list that means two things ────────────────────────────────────────
--
-- Seccl publishes `[{paymentDay, amount, movementType, method}]` with `movementType` In or Out — money
-- arriving by standing order and money leaving by regular withdrawal in the same array. The book has
-- separate `payments` and `withdrawals` columns, so the split happens here: one list per direction,
-- which is what the account record renders.

WITH details AS (
    SELECT
        cast(d.id AS varchar)                                            AS account_external_id,
        try_cast(d.adviser_charge AS decimal(12, 8))                      AS adviser_charge,
        -- Both nested values land as JSON strings (the records convention), so both are parsed here.
        coalesce(try_cast(d.recurring_payments AS json[]), [])            AS recurring,
        coalesce(
            try_cast(json_extract(d.adviser_tiered_charges, '$.tiers') AS json[]), []
        )                                                                AS adviser_tiers,
        d.auto_disinvest,
        json_extract_string(d.fee_structures, '$.adviserFeeId')           AS adviser_fee_package_key
    FROM raw.account_details AS d
),

accounts AS (
    SELECT
        cast(a.id AS varchar)                                            AS account_external_id,
        a.wrapper_detail ->> 'assetAllocationId'                          AS model_key
    FROM raw.accounts AS a
),

models AS (
    SELECT
        cast(m.id AS varchar)                                            AS model_key,
        m.name                                                           AS model_name,
        try_cast(m.model_charge AS decimal(12, 8))                        AS model_charge
    FROM raw.models AS m
),

-- One row per standing arrangement, split by direction.
recurring AS (
    SELECT
        d.account_external_id,
        -- `auto_disinvest` is the account's rule for RAISING the cash a withdrawal needs ('Largest'
        -- sells down the biggest holding). It is a property of the account, not of the payment, and it
        -- is meaningless on money coming IN — so it is carried only on the outbound rows, and the
        -- inbound ones state nothing rather than repeating a rule that does not apply to them.
        [
            {
                'amount': try_cast(r ->> 'amount' AS decimal(20, 2)),
                'payment_day': try_cast(r ->> 'paymentDay' AS int),
                'method': r ->> 'method',
                'movement_type': r ->> 'movementType',
                'auto_disinvest': CASE WHEN r ->> 'movementType' = 'Out' THEN d.auto_disinvest END,
                -- Seccl states no frequency: a `paymentDay` IS the frequency, and it is monthly.
                -- Stated rather than left NULL because the surface renders "14th of every month" and
                -- would otherwise have to invent the second half of that sentence itself.
                'frequency': 'Monthly'
            }
            FOR r IN d.recurring
            IF r ->> 'movementType' = 'In'
        ]                                                                AS payments,
        [
            {
                'amount': try_cast(r ->> 'amount' AS decimal(20, 2)),
                'payment_day': try_cast(r ->> 'paymentDay' AS int),
                'method': r ->> 'method',
                'movement_type': r ->> 'movementType',
                'auto_disinvest': d.auto_disinvest,
                'frequency': 'Monthly'
            }
            FOR r IN d.recurring
            IF r ->> 'movementType' = 'Out'
        ]                                                                AS withdrawals
    FROM details AS d
)

SELECT
    d.account_external_id,
    d.adviser_charge,
    -- Normalised tier bounds — see the note on the two spellings.
    [
        {
            'rate': try_cast(t ->> 'rate' AS decimal(12, 8)),
            'amount_from': try_cast(t ->> 'fromAmount' AS decimal(20, 2)),
            'amount_to': try_cast(t ->> 'toAmount' AS decimal(20, 2))
        }
        FOR t IN d.adviser_tiers
    ]                                                                    AS adviser_tiered_charges,
    m.model_charge,
    m.model_name,
    a.model_key,
    d.adviser_fee_package_key,
    d.auto_disinvest,
    -- ── The book's own `fees` shape ──────────────────────────────────────────────────────────────
    -- `platform.accounts.fees` is a jsonb list of `{name, type, frequency, rate, amount}` and the
    -- account record already renders exactly that. So the charges are shaped into it HERE rather than
    -- in a portal: one definition, and the admin book's read-only mount gets the same rows.
    --
    -- A charge the account does not carry contributes NO ROW, rather than a row with a NULL rate: an
    -- advisory account has no management fee, and "no management fee" and "a management fee of
    -- nothing" read very differently on a client's account.
    list_filter(
        [
            -- The adviser's own charge, flat where the firm prices it flat.
            CASE
                WHEN d.adviser_charge IS NOT NULL THEN {
                    'name': 'Adviser charge',
                    'type': 'adviser',
                    'frequency': 'Annual',
                    'rate': d.adviser_charge,
                    'amount': NULL::decimal(20, 2),
                    'basis': 'percentage'
                }
                -- ...or the first tier's rate where it is tiered, WITH the basis saying so. The tiers
                -- themselves travel in `adviser_tiered_charges` above; this row is the summary line the
                -- card shows, and calling it `tiered` is what stops it reading as the whole charge.
                WHEN len(d.adviser_tiers) > 0 THEN {
                    'name': 'Adviser charge',
                    'type': 'adviser',
                    'frequency': 'Annual',
                    'rate': try_cast(d.adviser_tiers[1] ->> 'rate' AS decimal(12, 8)),
                    'amount': NULL::decimal(20, 2),
                    'basis': 'tiered'
                }
            END,
            -- The DFM charge, which exists only where the account is managed to a model.
            CASE
                WHEN m.model_charge IS NOT NULL THEN {
                    'name': coalesce(m.model_name || ' management charge', 'Management charge'),
                    'type': 'dfm',
                    'frequency': 'Annual',
                    'rate': m.model_charge,
                    'amount': NULL::decimal(20, 2),
                    'basis': 'percentage'
                }
            END
        ],
        x -> x IS NOT NULL
    )                                                                    AS fees,
    r.payments,
    r.withdrawals,
    current_date                                                         AS as_of_date
FROM details AS d
LEFT JOIN accounts AS a ON a.account_external_id = d.account_external_id
LEFT JOIN models AS m ON m.model_key = a.model_key
LEFT JOIN recurring AS r ON r.account_external_id = d.account_external_id
