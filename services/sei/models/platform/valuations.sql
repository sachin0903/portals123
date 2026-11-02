/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: [account_external_id, as_of_date]
  depends_on: [raw.eod_positions, raw.eod_positions_supplemental, platform.instruments, platform.tenancy, platform.accounts, raw.accounts]
  tags: [sei, entity, conformed]
  description: >
    Account valuations by day — one row per account per drop, in the account's base
    currency. The series platform.accounts only ever held the latest of.
  checks:
    - not_null: account_external_id
    - not_null: as_of_date
    - not_null: market_value
*/

-- SEI publishes no account-level valuation report: EODPosValAggr is traded/settled *averages*
-- for fee computation, not a market value. So the valuation is summed from the taxlot positions,
-- which is what docs/platform-model.md §3 prescribes for this provider.
--
-- ============================================================================================
-- IT READS THE RAW REPORT, NOT platform.positions, AND THAT IS THE WHOLE POINT OF THIS MODEL.
-- ============================================================================================
--
-- `platform.positions` is CURRENT holdings — it windows to `max(dt)` because
-- `app/lake/holdings.py::_HOLDINGS_SQL` has no date filter and would otherwise render every
-- holding once per drop. Summing it therefore produced exactly ONE valuation per account, in a
-- table keyed `(account, as_of_date)` and indexed `(account_id, as_of_date DESC)` for a series
-- nothing could supply.
--
-- The history is real and already in the warehouse: `raw.eod_positions` merges on
-- `(_source_file, _row)`, so it holds every drop — 74 over 2025-10-29 → 2026-03-27. So this reads
-- the raw report across ALL dates. The cost is one aggregate over the full history instead of one
-- day; the gain is a value series for every account back to the first drop, which is what the
-- portal's performance chart, `opening_value` and every AUM tile need.
--
-- Contrast Seccl deliberately: its valuations model is `merge` on `current_date` because
-- "Seccl's summary endpoint returns the CURRENT valuation only; there is no history to re-read".
-- SEI's history is genuinely there, so it is read rather than accumulated.
--
-- MONEY IS IN THE ACCOUNT'S BASE CURRENCY (`VALUATION_CURRENCY` and the `VALUATION_*` columns),
-- because these are SUMMED and a total shown against an account must be in the account's own
-- money. SEI publishes each figure at three currency levels and the choice matters — the reasoning,
-- the measurements and the two exchange rates are in platform/positions.sql.
--
-- CASH IS SPLIT OUT NOW, from the position's own asset type. The note that stood here said an
-- is-currency flag was needed and EODPositions carries none, so `cash_value` was published NULL
-- for every SEI account while the book column existed and every portal read it. A currency holding
-- is `ASSET_TYPE = 'MRCXXXXXXX'`, which `platform.instruments` already publishes, and its
-- base-currency market value IS the cash — verified against SEI's own
-- `TRADED_CASH_BALANCE_IN_FBC` on the same 1,025 rows.
--
-- `market_value` is the WHOLE account including that cash, as it was before — `cash_value` is a
-- breakdown of it, not an addition to it. 0117's rule for readers stands: take a VALUE from one
-- source system, take CASH from all of them.
WITH supplemental AS (
    SELECT
        s.dt,
        s."ACCOUNT_NUMBER"                  AS account_number,
        s."PORTFOLIO_ID"                    AS portfolio_id,
        s."INSTRUMENT_ID"                   AS instrument_id,
        s."TRD_INSTRUMENT_ID"               AS trd_instrument_id,
        s."UNREALIZED_GAIN_LOSS_IN_FBC"     AS firm_unrealised_gain
    FROM raw.eod_positions_supplemental s
),
-- Joined on `dt` as well as the position key, because this reads the whole history and a position
-- must be valued against its OWN day's supplement. The key includes the traded instrument —
-- without it, two listings of one instrument id fan the join and this SUM doubles the account.
-- Verified in platform/positions.sql, and guarded there by `unique: position_key`.
history AS (
    SELECT
        p."ACCOUNT_NUMBER"                          AS account_number,
        -- Calendar day, not the raw value. The grain is the account and the day, so two
        -- timestamps on one day must not become two rows.
        cast(p."AS_OF_DATE" AS DATE)                AS as_of_date,
        p."VALUATION_CURRENCY"                      AS currency,
        (CASE WHEN p."DEBIT_CREDIT_FLAG_7" = 'D' THEN -1 ELSE 1 END)
            * p."VALUATION_CLEAN_MARKET_VALUE"      AS market_value,
        (CASE WHEN p."DEBIT_CREDIT_FLAG_10" = 'D' THEN -1 ELSE 1 END)
            * p."VALUATION_COST"                    AS book_cost,
        (CASE WHEN p."DEBIT_CREDIT_FLAG_6" = 'D' THEN -1 ELSE 1 END)
            * p."VALUATION_ACCRUED_INTEREST"        AS accrued_interest,
        -- SEI's stated gain, in the account's base currency, at the source scale.
        -- DuckDB's `/` on decimals returns DOUBLE. Summing those doubles is not the same
        -- bits on both `full_merge` reads: 158 account-days (14 Oct 2026) differed only
        -- here, by one unit in the last place, and both copies were stored. Round each
        -- lot to the report's scale first, then the sum is decimal and both reads agree.
        CASE WHEN p."LOCAL_TO_FIRM_EXCH_RATE" IS NOT NULL
                  AND p."LOCAL_TO_FIRM_EXCH_RATE" <> 0
             THEN CAST(round(
                    s.firm_unrealised_gain
                    * p."LOCAL_TO_BASE_EXCH_RATE" / p."LOCAL_TO_FIRM_EXCH_RATE",
                    10
                  ) AS decimal(38, 10))
        END                                         AS unrealised_gain,
        -- A currency holding's market value IS the account's cash at that currency; NULL on a
        -- security, so the sum below is the cash and nothing else.
        CASE WHEN i.asset_type = 'MRCXXXXXXX'
             THEN (CASE WHEN p."DEBIT_CREDIT_FLAG_7" = 'D' THEN -1 ELSE 1 END)
                  * p."VALUATION_CLEAN_MARKET_VALUE"
        END                                         AS cash_balance
    FROM raw.eod_positions p
    LEFT JOIN supplemental s
      ON  s.dt              = p.dt
      AND s.account_number  = p."ACCOUNT_NUMBER"
      AND s.portfolio_id    = p."TAXLOT_PORTFOLIO_ID"
      AND s.instrument_id   = p."INSTRUMENT_ID"
      AND s.trd_instrument_id IS NOT DISTINCT FROM p."TRD_INSTRUMENT_ID"
    LEFT JOIN platform.instruments i ON i.external_id = p."INSTRUMENT_ID"
    WHERE p."ACCOUNT_NUMBER" IS NOT NULL AND p."AS_OF_DATE" IS NOT NULL
),
-- The Accounts report states the SIPP split, and it states TODAY's split: a historical
-- valuation day must not be labelled with the current crystallised/uncrystallised pair.
-- Same grain as platform.accounts — latest drop, then the most recently updated portfolio
-- row — so the two models cannot disagree about which portfolio represents the account.
sipp AS (
    SELECT
        a."ACCOUNT_NUMBER" AS account_number,
        try_cast(a."SIPP_CRYSTALLISED_VALUE" AS decimal(38, 10)) AS crystallised_value,
        try_cast(a."SIPP_UNCRYSTALLISED_VALUE" AS decimal(38, 10)) AS uncrystallised_value
    FROM raw.accounts a
    WHERE a.dt = (SELECT max(dt) FROM raw.accounts)
    -- A total order. `full_merge` reads this query twice; a tie lets the two reads
    -- keep different SIPP rows and both get written under the same account and day.
    -- `IS NULL` rather than `NULLS LAST` because the warehouse parse drops `NULLS LAST`.
    -- `_source_file` / `_row` are raw.accounts' own key, so the pick cannot move
    -- between the two reads.
    QUALIFY row_number() OVER (
        PARTITION BY a."ACCOUNT_NUMBER"
        ORDER BY a."DATE_LAST_UPDATED" IS NULL, a."DATE_LAST_UPDATED" DESC,
                 a."PORTFOLIO_ID" IS NULL, a."PORTFOLIO_ID",
                 a._source_file IS NULL, a._source_file,
                 a._row IS NULL, a._row
    ) = 1
),
latest_day AS (
    SELECT account_number, max(as_of_date) AS as_of_date
    FROM history
    GROUP BY account_number
)
SELECT
    -- THE ORG COMES FROM THE ACCOUNT. platform.accounts resolves key/lineage once (see its
    -- header) and every account-grained relation selects it, so a fact and the account it is
    -- about cannot disagree about which node owns them. Recomputed here it had diverged, and
    -- one of the spellings of "unknown" was `[]` — which the fan-out reads as GLOBAL.
    -- `min`, not `any_value`. The grain is the account-day, so these are one value —
    -- except where a lot's currency is blank, or a join matched twice. `any_value`
    -- may pick either, and the two `full_merge` reads then disagree.
    min(acc.key)                                            AS key,
    min(acc.lineage)                                        AS lineage,
    NULL::timestamp                                         AS source_updated_at,
    h.account_number                                        AS account_external_id,
    h.as_of_date,
    sum(h.market_value)                                     AS market_value,
    -- `sum` of a column that is NULL on every security row is the cash and nothing else. An
    -- account holding no currency position sums to NULL, which is "SEI states no cash here"
    -- rather than "zero".
    sum(h.cash_balance)                                     AS cash_value,
    sum(h.book_cost)                                        AS book_cost,
    sum(h.unrealised_gain)                                  AS unrealised_gain,
    sum(h.accrued_interest)                                 AS accrued_interest,
    -- The account's value on the PREVIOUS drop, which is what a day's movement is measured from.
    -- Available now that this model reads the history; NULL on an account's first drop, because
    -- there is no earlier day to open from.
    lag(sum(h.market_value)) OVER (
        PARTITION BY h.account_number ORDER BY h.as_of_date
    )                                                       AS opening_value,
    -- The crystallisation split, from the Accounts report, on the latest valuation day only.
    -- Blank columns stay NULL: 0 would say the fund is wholly one side. A firm that leaves
    -- them empty (every profiled firm, as of the 2026-03-27 drop) therefore still publishes
    -- NULL, which is "not stated", not "SEI cannot express this".
    CASE WHEN max(ld.as_of_date) IS NOT NULL THEN min(sipp.crystallised_value) END
                                                        AS crystallised_value,
    CASE WHEN max(ld.as_of_date) IS NOT NULL THEN min(sipp.uncrystallised_value) END
                                                        AS uncrystallised_value,
    min(h.currency)                                         AS currency,
    count(*)                                                AS position_count,
    min(t.firm_id)                                          AS firm_id,
    min(t.subfirm_id)                                       AS subfirm_id,
    min(t.dsr_team_ids)                                     AS dsr_team_ids,
    to_json({'account_number': h.account_number, 'as_of_date': h.as_of_date}) AS data
FROM history h
LEFT JOIN platform.tenancy t ON t.account_number = h.account_number
LEFT JOIN platform.accounts acc ON acc.external_id = h.account_number
LEFT JOIN sipp ON sipp.account_number = h.account_number
LEFT JOIN latest_day ld
    ON ld.account_number = h.account_number AND ld.as_of_date = h.as_of_date
GROUP BY h.account_number, h.as_of_date
-- SEI can publish a taxlot with quantity and cost and no clean market value. Measured on
-- G086002 (firm 3110) 15-16 Sep 2026: 396.45 units, cost 105.59, VALUATION_CLEAN_MARKET_VALUE
-- blank. `sum` of those NULLs is NULL, `not_null: market_value` then blocks promotion of
-- every other account. A day SEI did not value is omitted — coalescing to 0 would show an
-- empty account that still holds the units. 22 of 125,392 lots in the corpus are blank;
-- only these two account-days have no valued lot at all.
HAVING sum(h.market_value) IS NOT NULL
