/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.eod_positions, raw.eod_positions_supplemental, platform.instruments, platform.tenancy, platform.accounts]
  tags: [sei, entity, conformed]
  description: >
    End-of-day holdings, one row per taxlot. Called `positions` to match Seccl's
    entity of the same name — SEI calls the concept EODPositions. Conformed to the
    shape app/lake/holdings.py reads, in the ACCOUNT's base currency.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: as_of_date
    - not_null: account_external_id
    - unique: position_key
*/

-- THIS MODEL PUBLISHED SIX OF THE FIFTEEN COLUMNS ITS ONLY READER NEEDS, and nobody noticed
-- because nothing read it: `book_sink.LAKE_ONLY["sei"]` was empty, so SEI's positions never
-- reached a tenant's lake at all. `app/lake/holdings.py::_HOLDINGS_SQL` — the query behind every
-- holdings table, allocation donut, cash figure and withdrawal gate — wants
--
--     account_external_id, instrument_key, asset_name, isin, currency, position_type,
--     market_value, quantity, price, book_cost, unrealised_gain, unrealised_gain_percent,
--     allocation, as_of_date  (+ sedol, joined from instruments)
--
-- and this published `account_number`, `instrument_id`, `cost` and nine absences. Projecting it
-- without conforming it would have turned an empty page into a Binder Error, which the merge
-- ledger records as "unavailable" — indistinguishable from "this firm has no data".
--
-- ============================================================================================
-- MONEY IS IN THE ACCOUNT'S BASE CURRENCY. SEI PUBLISHES THREE LEVELS; ONLY ONE IS RIGHT HERE.
-- ============================================================================================
--
-- Every reader SUMS these values: `_HOLDINGS_SQL`'s caller totals an account's positions, and
-- `platform.valuations` is `sum(market_value) GROUP BY account, date`. So they have to be in ONE
-- currency, and it has to be the currency the ACCOUNT is denominated in — `platform.accounts`
-- already carries `base_currency`, and a total shown against an account must be in the account's
-- own money.
--
-- SEI publishes each figure three times, and the three are genuinely different:
--
--   * ASSET / LOCAL   `ASSET_CURRENCY`, `MARKET_VALUE_IN_ASSET_CURRENCY` — the instrument's own
--                     currency. Six of them in the 2026-03-27 drop (GBP 25,229, USD 576, EUR 75,
--                     CHF 27, HKD 19, JPY 4). **This is what the model used to publish**, so 703
--                     of 25,932 positions were being added to sterling as though dollars were
--                     pounds and every account total derived from them was wrong.
--   * ACCOUNT BASE    `VALUATION_CURRENCY` + the `VALUATION_*` columns. GBP 25,906 / USD 26 —
--                     two accounts (MSP000073, MSP000074) are USD-based. **This is the one.**
--   * FIRM BASE       `FIRM_BASE_CURRENCY` + the supplemental's `*_IN_FBC` columns. GBP for all
--                     six firms. Right for a firm-wide aggregate and wrong for an account: it
--                     would state a USD account's holdings in sterling.
--
-- The two are not interchangeable, and SEI says so itself by publishing two rates:
-- `LOCAL_TO_BASE_EXCH_RATE` differs from `LOCAL_TO_FIRM_EXCH_RATE` on exactly the 26 rows whose
-- `VALUATION_CURRENCY` is USD.
--
-- ============================================================================================
-- THE GAIN IS SEI'S, NOT OURS. Do not "simplify" this to market_value - book_cost.
-- ============================================================================================
--
-- That was the obvious derivation and it is wrong. Measured against SEI's own
-- `UNREALIZED_GAIN_LOSS_IN_FBC` over the 2026-03-27 drop (restricted to the GBP-base rows, where
-- firm and base currency coincide so the comparison is fair), every candidate pairing disagrees:
--
--     VALUATION_CLEAN_MARKET_VALUE  - VALUATION_COST                    holds 24,260  fails 624
--     TRADED_MARKET_VAL_IN_FBC      - TRADED_CURRENT_COST_IN_FBC        holds 24,271  fails 635
--     TRADED_MARKET_VAL_IN_FBC      - TRADED_INVESTMENT_COST_IN_FBC     holds 24,271  fails 635
--     SETTLED_MARKET_VALUE_IN_BASE  - SETTLED_ORIGINAL_COST_IN_BASE     holds 14,423  fails 10,461
--
-- The clean-vs-dirty market value was the obvious explanation for the 635 and it is not that
-- either: the clean pair fails 624. And the failures are not rounding — one row derives +2,770.40
-- where SEI says -13,767.56. SEI computes it against something we are not given. A derived figure
-- that contradicts the custodian on 2.5% of positions is worse than no figure, because it is the
-- number a client reads as their gain.
--
-- So it is SEI's figure, CONVERTED with SEI's own rates rather than re-derived. The gain is
-- published in firm currency only, and `value_in_base = value_in_firm * (base_rate / firm_rate)`
-- follows from the rate semantics, which are verified rather than assumed:
-- `local_value * LOCAL_TO_FIRM_EXCH_RATE = TRADED_MARKET_VAL_IN_FBC` holds on all 25,932 rows with
-- zero failures. For the 25,906 rows where the two rates are equal the ratio is 1 and the
-- conversion is a no-op.
--
-- `TRADED_BOOK_COST_BASE` and `SETTLED_BOOK_COST_BASE` look like the book cost and are **0%
-- populated in every drop of the corpus** — `VALUATION_COST` (96%) is the one that carries it.
--
-- ============================================================================================
-- CASH IS AN ASSET TYPE, AND IT COMES OFF THE POSITION ITSELF.
-- ============================================================================================
--
-- A currency holding is `ASSET_TYPE = 'MRCXXXXXXX'` with a blank ISIN and a currency for a name
-- (7 such instruments, 1,025 position rows, £10.2m on 2026-03-27). Its base-currency market value
-- IS the account's cash at that currency — checked against the supplemental's own
-- `TRADED_CASH_BALANCE_IN_FBC`: the same 1,025 rows on both sides, agreeing on 1,021, and the four
-- that differ are precisely the USD-base rows where the position's figure is in USD and SEI's is in
-- sterling (104,903.06 vs 78,930.86 — the ratio is the base rate). So the position is the better
-- source, and it needs no join.
--
-- DO NOT infer cash from a missing ISIN. 8,755 of 20,521 asset rows have none and 8,486 of those
-- are 'MMFXXXXXXX' MONEY-MARKET FUNDS, which are not cash — the heuristic that yields zero cash on
-- Seccl yields a nine-figure over-count here. `instructions.is_cash_holding` reads `position_type`
-- plus the name, and both are published for it.
--
-- ============================================================================================
-- THE SUPPLEMENTAL JOIN IS 1:1 ON THE TRADED INSTRUMENT, AND `unique: position_key` KEEPS IT SO.
-- ============================================================================================
--
-- EODPositions is per TAXLOT. The supplemental carries no taxlot id. Its grain is
-- (account, portfolio, instrument, TRD_INSTRUMENT_ID) — the traded instrument, which is the
-- listing. Measured on 2026-03-27 that coincided with (account, portfolio, instrument): 25,932
-- rows and 25,932 distinct keys on both sides, none absent from either, no key carrying more
-- than one taxlot. Production 2026-10-06 did not. Two instruments each had two listings
-- (different TRD_INSTRUMENT_ID, SEDOL, quantity and cost) and a blank TAXLOT_ID, and the
-- supplemental had two rows for each of those coarser keys.
--
-- Joining without the traded instrument fans 2×2: each line is paired with both gains.
-- `concat_ws` then makes it worse. It skips nulls, so a blank TAXLOT_ID drops out of
-- `external_id` and the two listings share one key. `coalesce` to '' keeps that empty segment,
-- so a blank taxlot cannot slide the traded instrument into the taxlot's place.
--
-- `position_key` is the supplement's grain and does NOT carry the taxlot. Two taxlots of one
-- traded instrument still collide, the unique check fails, and the night stops — each lot would
-- otherwise take a full copy of the supplement and double the gain. A firm's SEI data
-- unavailable for a night is recoverable. A doubled gain on a client's statement is what
-- somebody acts on.
--
-- The unsigned-amount convention is unchanged: sixteen DEBIT_CREDIT_FLAGs, each qualifying the
-- money column before it — _6 the valuation accrued interest, _7 the clean market value, _10 the
-- valuation cost. The supplemental's FBC columns carry their own sign (a negative cash balance
-- appears as `-278.51`) and take none.
WITH latest AS (SELECT max(dt) AS dt FROM raw.eod_positions),
current AS (SELECT p.* FROM raw.eod_positions p JOIN latest ON p.dt = latest.dt),
latest_supp AS (SELECT max(dt) AS dt FROM raw.eod_positions_supplemental),
supplemental AS (
    SELECT s.*
    FROM raw.eod_positions_supplemental s
    JOIN latest_supp ON s.dt = latest_supp.dt
),
joined AS (
    SELECT
        p.*,
        -- SEI's own gain, expressed in the ACCOUNT's base currency by its own two rates. The
        -- ratio is 1 wherever base and firm currency coincide, which is 25,906 of 25,932 rows.
        CASE WHEN p."LOCAL_TO_FIRM_EXCH_RATE" IS NOT NULL
                  AND p."LOCAL_TO_FIRM_EXCH_RATE" <> 0
             THEN s."UNREALIZED_GAIN_LOSS_IN_FBC"
                  * p."LOCAL_TO_BASE_EXCH_RATE" / p."LOCAL_TO_FIRM_EXCH_RATE"
        END                                     AS base_unrealised_gain
    FROM current p
    LEFT JOIN supplemental s
      ON  s."ACCOUNT_NUMBER" = p."ACCOUNT_NUMBER"
      AND s."PORTFOLIO_ID"   = p."TAXLOT_PORTFOLIO_ID"
      AND s."INSTRUMENT_ID"  = p."INSTRUMENT_ID"
      -- NULL = NULL is not true. A blank traded instrument on both sides is still one holding.
      AND s."TRD_INSTRUMENT_ID" IS NOT DISTINCT FROM p."TRD_INSTRUMENT_ID"
),
-- The money, signed and named once, so the allocation window and the cash split read the same
-- expression the projection publishes rather than a second copy of it.
signed AS (
    SELECT
        j.*,
        (CASE WHEN j."DEBIT_CREDIT_FLAG_7" = 'D' THEN -1 ELSE 1 END)
            * j."VALUATION_CLEAN_MARKET_VALUE"  AS base_market_value,
        (CASE WHEN j."DEBIT_CREDIT_FLAG_10" = 'D' THEN -1 ELSE 1 END)
            * j."VALUATION_COST"                AS base_book_cost,
        (CASE WHEN j."DEBIT_CREDIT_FLAG_6" = 'D' THEN -1 ELSE 1 END)
            * j."VALUATION_ACCRUED_INTEREST"    AS base_accrued_interest
    FROM joined j
)
SELECT
    -- The conformed spine (docs/platform-model.md §3), published alongside the existing
    -- tenancy columns rather than instead of them — see platform/accounts.sql for why the
    -- fan-out switch is a separate step.
    -- A taxlot is unique only within an account, portfolio, instrument, traded instrument and
    -- day. The portfolio was missing from the old key (43 collisions). The traded instrument
    -- was missing from this one: a blank TAXLOT_ID is skipped by concat_ws, so two listings
    -- of one instrument id became one key. '' keeps the blank taxlot's place.
    concat_ws(':', s."ACCOUNT_NUMBER", s."TAXLOT_PORTFOLIO_ID", s."INSTRUMENT_ID",
              coalesce(s."TAXLOT_ID", ''), coalesce(s."TRD_INSTRUMENT_ID", ''),
              strftime(s."AS_OF_DATE", '%Y%m%d')) AS external_id,
    -- THE ORG COMES FROM THE ACCOUNT. platform.accounts resolves key/lineage once (see its
    -- header) and every account-grained relation selects it, so a fact and the account it is
    -- about cannot disagree about which node owns them. Recomputed here it had diverged, and
    -- one of the spellings of "unknown" was `[]` — which the fan-out reads as GLOBAL.
    acc.key,
    acc.lineage,
    NULL::timestamp                                 AS source_updated_at,
    to_json(s)                                      AS data,

    -- The join key every reader resolves an account by. `account_number` is kept beside it
    -- because the lake-side readers already select that name.
    s."ACCOUNT_NUMBER"                              AS account_external_id,
    s."ACCOUNT_NUMBER"                              AS account_number,
    -- The SUPPLEMENT's grain, declared `unique` above. No taxlot: two lots of one traded
    -- instrument still collide, which is the tripwire for a split fanning one gain onto both.
    concat_ws(':', s."ACCOUNT_NUMBER", s."TAXLOT_PORTFOLIO_ID", s."INSTRUMENT_ID",
              coalesce(s."TRD_INSTRUMENT_ID", ''))
                                                    AS position_key,
    s."TAXLOT_PORTFOLIO_ID"                         AS portfolio_id,
    s."TAXLOT_ID"                                   AS taxlot_id,

    -- The instrument, under the conformed names AND SEI's own.
    s."INSTRUMENT_ID"                               AS instrument_key,
    s."INSTRUMENT_ID"                               AS instrument_id,
    i.name                                          AS asset_name,
    i.isin                                          AS isin,
    s."SEDOL"                                       AS sedol,
    i.asset_type                                    AS position_type,

    -- Dates and amounts arrive typed from raw.eod_positions (sei_sync.schema). While
    -- as_of_date was cast here it was NULL for every row — SEI writes 20260327, which
    -- CAST cannot read — and it is part of this model's key.
    s."AS_OF_DATE"                                  AS as_of_date,
    s."QUANTITY_HELD"                               AS quantity,

    -- MONEY, in the ACCOUNT's base currency. See the header.
    s."VALUATION_CURRENCY"                          AS currency,
    s.base_market_value                             AS market_value,
    s.base_book_cost                                AS book_cost,
    s.base_unrealised_gain                          AS unrealised_gain,
    s.base_accrued_interest                         AS accrued_interest,
    -- Against COST, which is what a return is. NULL rather than 0 when there is no cost to measure
    -- against: a 0% gain and an unmeasurable one are different answers, and `_holding_return` in
    -- routers/crm.py already discards the provider's percentage when it is implausible.
    CASE WHEN s.base_book_cost IS NOT NULL AND s.base_book_cost <> 0
         THEN s.base_unrealised_gain / s.base_book_cost
    END                                             AS unrealised_gain_percent,
    -- The cash leg, in the account's own currency: a currency holding's market value IS the cash.
    -- NULL on a security, so summing this column at account level is the cash and nothing else.
    CASE WHEN i.asset_type = 'MRCXXXXXXX' THEN s.base_market_value END
                                                    AS cash_balance,
    -- SEI publishes no opening value on this report; platform.valuations derives it from the
    -- previous drop.
    NULL::decimal(38, 10)                           AS opening_value,

    -- A PRICE IS QUOTED IN THE INSTRUMENT'S OWN CURRENCY, not the account's — which is how every
    -- custodian statement reads, and why the currency is published beside it rather than assumed
    -- to be the account's. `quantity * price` is therefore the LOCAL market value, not
    -- `market_value`; that is the same relationship SEI's own report has.
    s."ASSET_PRICE"                                 AS price,
    s."ASSET_CURRENCY"                              AS price_currency,

    -- This holding's share of its ACCOUNT, as a fraction — the same basis Seccl publishes, so the
    -- allocation donut reads one number from either custodian. Over the account rather than the
    -- portfolio: an account is what the portal shows. Safe to window over the account because
    -- every value in it is now in that account's one currency.
    CASE WHEN sum(s.base_market_value) OVER (PARTITION BY s."ACCOUNT_NUMBER") <> 0
         THEN s.base_market_value
              / sum(s.base_market_value) OVER (PARTITION BY s."ACCOUNT_NUMBER")
    END                                             AS allocation,

    -- SEI's own asset-currency figures, kept because they are what its statements show a client,
    -- and its firm-currency ones, because that is the basis a firm-wide aggregate needs.
    s."ASSET_CURRENCY"                              AS local_currency,
    (CASE WHEN s."DEBIT_CREDIT_FLAG_1" = 'D' THEN -1 ELSE 1 END)
        * s."MARKET_VALUE_IN_ASSET_CURRENCY"        AS local_market_value,
    (CASE WHEN s."DEBIT_CREDIT_FLAG_5" = 'D' THEN -1 ELSE 1 END)
        * s."DEALING_COST"                          AS local_cost,
    -- `cost` is the name the pre-existing lake-side readers select.
    (CASE WHEN s."DEBIT_CREDIT_FLAG_5" = 'D' THEN -1 ELSE 1 END)
        * s."DEALING_COST"                          AS cost,
    s."LOCAL_TO_BASE_EXCH_RATE"                     AS local_to_base_rate,
    s."LOCAL_TO_FIRM_EXCH_RATE"                     AS local_to_firm_rate,

    t.firm_id,
    t.subfirm_id,
    t.dsr_team_ids,
    t.adviser_firm_dsr_ids
FROM signed s
LEFT JOIN platform.tenancy t ON t.account_number = s."ACCOUNT_NUMBER"
LEFT JOIN platform.accounts acc ON acc.external_id = s."ACCOUNT_NUMBER"
LEFT JOIN platform.instruments i ON i.external_id = s."INSTRUMENT_ID"
