/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.assets, raw.costs_charges, platform.emt_charges]
  tags: [sei, entity, conformed]
  description: >
    The instrument universe -- `instruments` is the canonical §3 name; SEI's own report is
    called Assets and `raw.assets` keeps that name. Firm-wide reference data — deliberately carries NO tenancy
    columns: an instrument is not owned by a tenant, and filtering it by DSR would
    empty a table every tenant legitimately needs in full.
  checks:
    - not_null: external_id
    - unique: external_id
*/
-- GRAIN. `key: instrument_id` was a false claim: the latest drop carries 6,123 duplicate
-- INSTRUMENT_IDs, so any join to this model multiplied rows. The Assets report repeats the
-- instrument block per related record (classification / exchange listing), the same shape
-- Accounts and Clients have. This publishes ONE ROW PER INSTRUMENT.
--
-- THE ORDER IS A TOTAL ORDER, not a preference. `full_merge` reads this query twice — once
-- to find keys whose content changed, again to insert them. A tie on (name, ISIN, SEDOL)
-- lets those two reads keep different listings of the same INSTRUMENT_ID, and both land:
-- `unique: external_id` then fails and the whole run's promotion is blocked. `_source_file`
-- and `_row` are raw.assets' own merge key, so the pick cannot move between the two reads.
-- Name / ISIN / SEDOL stay first so a populated listing still beats a blank one; the file
-- and row only break the tie.
with latest as (select max(dt) as dt from raw.assets),
current as (
  select a.* from raw.assets a join latest on a.dt = latest.dt
  qualify row_number() over (
    partition by a."INSTRUMENT_ID"
    -- `IS NULL` rather than `NULLS LAST`: interlace's sqlglot drops `NULLS LAST` on the
    -- way to DuckDB, which would let a blank listing sort first and win the pick.
    order by a."ASSET_NAME" is null, a."ASSET_NAME",
             a."ISIN" is null, a."ISIN",
             a."SEDOL_NUMBER" is null, a."SEDOL_NUMBER",
             a._source_file, a._row
  ) = 1
),
-- The ongoing charge is the fund's own figure, repeated on each firm's CostsCharges
-- row. An instrument is global, so this collapses to one number: the actual when every
-- firm that published one published the same one, otherwise the estimate on that same
-- rule. A disagreement, or a blank file, stays NULL — picking a firm would invent a charge.
-- platform.emt_charges is the firm's EMT extract, already one charge per ISIN. It fills
-- that gap and does not override an agreed CostsCharges figure: the nightly drop remains
-- the source when the firms agree.
charge_latest as (select max(dt) as dt from raw.costs_charges),
charges as (
  select
    c."INSTRUMENT_ID" as instrument_id,
    case
      when count(distinct c."ONGOING_CHARGE_ACTUAL") = 1 then max(c."ONGOING_CHARGE_ACTUAL")
      when count(c."ONGOING_CHARGE_ACTUAL") = 0
       and count(distinct c."ONGOING_CHARGE_ESTIMATE") = 1 then max(c."ONGOING_CHARGE_ESTIMATE")
    end as ocf
  from raw.costs_charges c
  join charge_latest on c.dt = charge_latest.dt
  group by c."INSTRUMENT_ID"
)
select
  -- The conformed spine (docs/platform-model.md §3). lineage is EMPTY on purpose: an
  -- instrument is not owned by a firm, and an empty path is how the conformed predicate
  -- says "global reference data, project whole" — the thing _SEI_GLOBAL_TABLES hand-codes.
  a."INSTRUMENT_ID"                        as external_id,
  ''                                       as key,
  []::varchar[]                            as lineage,
  null::timestamp                          as source_updated_at,
  to_json(a)                               as data,
  a."INSTRUMENT_ID"                        as instrument_id,
  a."ASSET_NAME"                           as name,
  -- SEI's asset report carries no separate legal name. Explicit NULL rather than omitted, so
  -- the conformed column set stays fixed for every provider (§3).
  null::varchar                             AS legal_name,
  a."ASSET_TYPE"                           as asset_type,
  a."ISIN"                                 as isin,
  a."SEDOL_NUMBER"                         as sedol,
  a."CUSIP"                                as cusip,
  a."ISO_CURRENCY"                         as currency,
  a."STOCK_EXCHANGE_CODE"                  as exchange_code,
  a."MARKET_COUNTRY"                       as market_country,
  a."ISSUER_COUNTRY"                       as issuer_country,
  a."CREDIT_RATING"                        as credit_rating,
  -- Typed in raw.assets (sei_sync.schema); cast here the two dates were NULL for
  -- every row, SEI writing them as YYYYMMDD which CAST cannot read.
  a."INTEREST_RATE"                        as interest_rate,
  a."ISSUE_DATE"                           as issued_on,
  a."MATURITY_DATE"                        as matures_on,
  -- As published. Not rescaled: the file's 0.77 is 0.77, and a blank is NULL.
  -- CostsCharges when the firms agree, otherwise the EMT extract for this ISIN.
  cast(coalesce(ch.ocf, emt.ocf) as decimal(18, 10)) as ocf,
  a.dt                                     as as_of_date
from current a
left join charges ch on ch.instrument_id = a."INSTRUMENT_ID"
LEFT JOIN platform.emt_charges emt ON emt.isin = a."ISIN"
