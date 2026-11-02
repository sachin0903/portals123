/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.accounts, platform.nodes]
  tags: [seccl, entity, conformed]
  description: >
    Current holdings, conformed — one row per position (composite id
    account|asset|ccy), the nightly account-list baseline unnested from
    accounts.positions[] overlaid with realtime position webhooks.
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- NO WEBHOOK OVERLAY. The staging tables are event ENVELOPES, not records: verified against
-- staging, `staging.positions` carries only (_event_key, firm_id, id, account_id, position_type,
-- update_date, _offset, _ingested_at), `staging.clients` only (client_id, update_date) and
-- `staging.transactions` only (id, transaction_type, status, update_date). The previous
-- `union all by name` over the baseline therefore did the opposite of what it looked like: a
-- record with a webhook event was EXCLUDED from the baseline by the NOT EXISTS and replaced by
-- a stub whose every other column was NULL. It is latent today only because the streams are
-- empty. The stream is a change SIGNAL — what it should drive is a targeted re-fetch of the
-- changed id, not a substitution — so until that exists the baseline is the record.
--
-- Holdings stay in the lake and are NOT materialised into OLTP
-- (docs/platform-model.md §2, Rule A): account × instrument × date is high volume and a
-- pure snapshot, and app/lake already serves it to the portals. The conformed shape
-- still applies, so the same reader works across providers.
--
-- Cash positions carry no asset fields (positionType='Cash'), which is why
-- instrument_key and isin are nullable here and a NULL instrument is not a defect.
WITH baseline AS (
    -- node_id is scalar-or-list: Seccl sends `["9"]` on clients and `41` on positions (the
-- quirk app/platforms.py::_SLICE and fanout._predicate both handle). Taking the first
-- element of the list form, or the scalar as-is, is what makes key one value.
SELECT
        p.value ->> 'id'                                        AS external_id,
        cast(a.id AS varchar)                                   AS account_external_id,
        coalesce(try_cast(cast(a.node_id AS varchar) AS varchar[])[1], cast(a.node_id AS varchar))
                                                                AS key,
        p.value ->> 'positionType'                              AS position_type,
        p.value ->> 'assetId'                                   AS instrument_key,
        p.value ->> 'assetName'                                 AS asset_name,
        p.value ->> 'isin'                                      AS isin,
        coalesce(p.value ->> 'currency', 'GBP')                 AS currency,
        try_cast(p.value ->> 'quantity' AS decimal(28, 8))      AS quantity,
        try_cast(p.value ->> 'currentPrice' AS decimal(20, 8))  AS price,
        try_cast(p.value ->> 'currentValue' AS decimal(20, 4))  AS market_value,
        try_cast(p.value ->> 'bookValue' AS decimal(20, 4))     AS book_cost,
        try_cast(p.value ->> 'transferBookValue' AS decimal(20, 4)) AS transfer_book_cost,
        try_cast(p.value ->> 'growth' AS decimal(20, 4))        AS unrealised_gain,
        try_cast(p.value ->> 'growthPercent' AS double)         AS unrealised_gain_percent,
        try_cast(p.value ->> 'adjustedGrowth' AS decimal(20, 4)) AS adjusted_gain,
        try_cast(p.value ->> 'adjustedGrowthPercent' AS double)  AS adjusted_gain_percent,
        try_cast(p.value ->> 'openingValue' AS decimal(20, 4))  AS opening_value,
        try_cast(p.value ->> 'allocation' AS double)            AS allocation,
        try_cast(p.value ->> 'currentPriceDate' AS date)        AS price_date,
        NULL::timestamp                                         AS source_updated_at,
        p.value                                                 AS data
    FROM raw.accounts AS a, json_each(a.positions) AS p
),
combined AS (
    SELECT * FROM baseline
)
SELECT
    c.*,
    -- `o.lineage`, NOT `coalesce(o.lineage, [])`, which is what this said. An EMPTY lineage is
    -- the fan-out's GLOBAL sentinel (`book_sink.predicate.conformed`: "an empty lineage means
    -- global reference data … which projects whole") and Seccl IS filtered by containment
    -- today — so a row whose node this provider's tree cannot resolve was projected into EVERY
    -- enabled tenant's slice rather than into none. NULL is what "no org" spells: the
    -- predicate tests it to false. (0 rows on the dev book, where all 376 accounts resolve a
    -- node; the hole is one unresolvable node wide, and it takes that account's positions,
    -- transactions and valuations with it.)
    o.lineage                    AS lineage,
    coalesce(c.price_date, current_date) AS as_of_date
FROM combined AS c
LEFT JOIN platform.nodes AS o ON o.key = c.key
