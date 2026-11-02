/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: raw.assets
  tags: [seccl, entity, conformed]
  description: >
    The asset universe, conformed. Reference data shared by every tenant, so lineage
    is EMPTY on purpose — an instrument is not owned by a firm, and narrowing it by
    node would empty a table every tenant needs in full.
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- An empty lineage is the conformed way to say "global reference data". app/fanout.py
-- projects such a row unfiltered, which is what its hand-maintained
-- _SEI_GLOBAL_TABLES set does today for SEI's assets.
--
-- Verified against staging (134 assets): Seccl publishes no SEDOL, CUSIP or
-- `primaryMarket` — the documented Postman shape claimed the last of these and the live
-- API does not have it, which is why this is written against the warehouse, not the docs.
-- Seccl carries no SEDOL or CUSIP on an asset (it has mexId, ticker and ptm instead),
-- so those columns are published NULL rather than omitted: the materialiser reads one
-- column list for every provider, and SEI does supply them.
SELECT
    cast(a.id AS varchar)                                   AS external_id,
    NULL::varchar                                           AS key,
    []::varchar[]                                           AS lineage,
    -- longName is the registered name; short/custom names are what an adviser sees in
    -- Seccl's own UI, so they are the better label when present.
    coalesce(a.custom_name, a.short_name, a.long_name, a.id) AS name,
    a.long_name                                             AS legal_name,
    coalesce(a.instrument_type, a.type)                     AS asset_type,
    a.isin,
    NULL::varchar                                           AS sedol,
    NULL::varchar                                           AS cusip,
    a.currency,
    -- MIC is the ISO market identifier; SEI publishes an exchange code in the same role.
    a.mic                                                   AS exchange_code,
    a.country_of_issue                                      AS market_country,
    NULL::varchar                                           AS issuer_country,
    NULL::varchar                                           AS credit_rating,
    NULL::double                                            AS interest_rate,
    NULL::date                                              AS issued_on,
    NULL::date                                              AS matures_on,
    -- SEI's CostsCharges file is the ongoing charge. Seccl's asset payload does not
    -- carry one, and a NULL here must not wipe a figure another custodian already stored.
    NULL::decimal(18, 10)                                   AS ocf,
    a.status                                                AS status_raw,
    try_cast(a.audit_details ->> 'updateDate' AS timestamp) AS source_updated_at,
    coalesce(try_cast(a.audit_details ->> 'updateDate' AS date), current_date) AS as_of_date,
    to_json(a)                                              AS data
FROM raw.assets AS a
