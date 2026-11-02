/*
interlace:
  materialise: virtual
  strategy: merge
  key: [external_id, as_of_date]
  depends_on: [raw.accounts, raw.account_details, platform.nodes]
  tags: [seccl, entity, conformed]
  description: >
    Account valuations, one row per account per date — the time series
    platform.accounts.value and the portals' growth figures are derived from.
  checks:
    - not_null: external_id
    - not_null: as_of_date
    - not_null: market_value
*/

-- WHY merge AND NOT replace. Seccl's summary endpoint returns the CURRENT valuation
-- only; there is no history to re-read. So each run contributes one row per account for
-- the date it ran, and the key (account, date) accumulates a series that a full refresh
-- would destroy. This is the one conformed model whose grain is not "current state".
--
-- Read from the account list, which carries the whole valuation block (current_value,
-- uninvested_cash, book_value, growth, opening_value — verified against staging). The
-- per-account `/account/summary` fan-out repeats the same fields and is optional, so depending
-- on it only made this model unbuildable when that pull had not run.
--
-- node_id is scalar-or-list: Seccl sends `["9"]` on clients and `41` on positions (the
-- quirk app/platforms.py::_SLICE and fanout._predicate both handle). Taking the first
-- element of the list form, or the scalar as-is, is what makes key one value.
WITH source AS (
    SELECT
        cast(a.id AS varchar)                                       AS account_external_id,
        coalesce(try_cast(cast(a.node_id AS varchar) AS varchar[])[1], cast(a.node_id AS varchar))
                                                                    AS key,
        coalesce(a.currency, 'GBP')                                 AS currency,
        try_cast(a.current_value AS decimal(20, 4))                      AS market_value,
        try_cast(a.uninvested_cash AS decimal(20, 4))                      AS cash_value,
        try_cast(a.book_value AS decimal(20, 4))                      AS book_cost,
        try_cast(a.growth AS decimal(20, 4))                      AS unrealised_gain,
        try_cast(a.opening_value AS decimal(20, 4))                      AS opening_value,
        NULL::timestamp                                             AS source_updated_at,
        to_json(a)                                                  AS data
    FROM raw.accounts AS a
),
-- An account sits in one portfolio group. `groupType` is the crystallisation: the
-- account's current value is wholly that side. Two disagreeing groups on one account
-- publish neither figure rather than a guess. Non-pensions are excluded here so a GIA
-- that happens to carry a group is not given a pension split.
--
-- The group is on the account detail (`GET /account/{id}` → raw.account_details).
-- The account list does not carry it, so reading `raw.accounts.portfolio_groups`
-- binds against a column the extract does not have and the split stays blank.
pension_groups AS (
    SELECT
        cast(a.id AS varchar) AS account_id,
        CASE
            WHEN count(DISTINCT upper(json_extract_string(item, '$.groupType'))) = 1
                THEN any_value(upper(json_extract_string(item, '$.groupType')))
        END AS group_type
    FROM raw.accounts AS a
    JOIN raw.account_details AS d
        ON cast(d.id AS varchar) = cast(a.id AS varchar)
    CROSS JOIN LATERAL unnest(coalesce(try_cast(d.portfolio_groups AS json[]), [])) AS u(item)
    WHERE upper(coalesce(
        a.wrapper_detail ->> 'wrapperType',
        nullif(a.account_type, 'Wrapper')
    )) IN ('PENSION', 'SIPP', 'TPPENSION')
    GROUP BY 1
)
SELECT
    src.account_external_id                       AS external_id,
    src.account_external_id,
    src.key,
    -- `o.lineage`, NOT `coalesce(o.lineage, [])`, which is what this said. An EMPTY lineage is
    -- the fan-out's GLOBAL sentinel (`book_sink.predicate.conformed`: "an empty lineage means
    -- global reference data … which projects whole") and Seccl IS filtered by containment
    -- today — so a row whose node this provider's tree cannot resolve was projected into EVERY
    -- enabled tenant's slice rather than into none. NULL is what "no org" spells: the
    -- predicate tests it to false. (0 rows on the dev book, where all 376 accounts resolve a
    -- node; the hole is one unresolvable node wide, and it takes that account's positions,
    -- transactions and valuations with it.)
    o.lineage                                   AS lineage,
    -- The valuation is asserted as of the day it was read. Seccl stamps no valuation
    -- date of its own, and dating it by the account's updateDate would file today's
    -- figure under the day the account record last changed.
    current_date                                  AS as_of_date,
    src.market_value,
    src.cash_value,
    src.book_cost,
    src.unrealised_gain,
    src.opening_value,
    -- Crystallised / uncrystallised from `portfolioGroups[].groupType`. The empty side is 0
    -- only once the group is known, so both tiles can render; an account with no group (or
    -- two that disagree) stays NULL on both, which is "not stated". Beneficiary and a
    -- crystallised transfer are drawdown already taken, so they sit with crystallised.
    CASE
        WHEN grp.group_type IN ('CRYSTALLISED', 'CRYSTALLISEDTRANSFER', 'BENEFICIARY')
            THEN src.market_value
        WHEN grp.group_type = 'UNCRYSTALLISED' THEN 0::decimal(20, 4)
    END                                           AS crystallised_value,
    CASE
        WHEN grp.group_type = 'UNCRYSTALLISED' THEN src.market_value
        WHEN grp.group_type IN ('CRYSTALLISED', 'CRYSTALLISEDTRANSFER', 'BENEFICIARY')
            THEN 0::decimal(20, 4)
    END                                           AS uncrystallised_value,
    src.currency,
    src.source_updated_at,
    src.data
FROM source AS src
LEFT JOIN platform.nodes AS o ON o.key = src.key
LEFT JOIN pension_groups AS grp ON grp.account_id = src.account_external_id
-- An account with no valuation on either source contributes nothing: a valuation row
-- with a NULL market_value would be indistinguishable from a genuinely empty account.
WHERE src.market_value IS NOT NULL
