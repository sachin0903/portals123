/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.models, platform.nodes]
  tags: [seccl, entity, conformed]
  description: >
    Investment models, conformed. Seccl publishes the whole thing — aim, risk rating, charge,
    the allocation lines and the commentary — so the canonical model and its version history
    can be materialised from one relation.
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- Verified against staging: 51 models, each carrying `allocations` (the lines), `asset_mix`
-- (the instrument-type split) and `commentary` as nested JSON. Those three stay JSON here
-- rather than being unnested into rows: the materialiser expands them into
-- platform.model_versions / model_allocations / model_commentary, which is where the versioned
-- grain belongs. Unnesting here would publish a relation whose grain is a line, and the
-- conformed contract is one row per entity.
--
-- TARGET RETURN. Seccl has no field for it, which is why firms put it in `url` — that field is
-- not even in the staging shape, so nothing is read for it here. It stays a Graphene-side
-- value, edited in the admin catalogue and left alone by the sync (the materialiser must not
-- overwrite a target return with NULL because the provider has no opinion on it).
--
-- node_id is scalar-or-list: Seccl sends `["9"]` on some resources and `41` on others (the
-- quirk app/platforms.py::_SLICE and fanout._predicate both handle).
SELECT
    cast(m.id AS varchar)                                   AS external_id,
    coalesce(try_cast(cast(m.node_id AS varchar) AS varchar[])[1], cast(m.node_id AS varchar))
                                                            AS key,
    -- `o.lineage`, NOT `coalesce(o.lineage, [])`, which is what this said. An EMPTY lineage is
    -- the fan-out's GLOBAL sentinel (`book_sink.predicate.conformed`: "an empty lineage means
    -- global reference data … which projects whole") and Seccl IS filtered by containment
    -- today — so a row whose node this provider's tree cannot resolve was projected into EVERY
    -- enabled tenant's slice rather than into none. NULL is what "no org" spells: the
    -- predicate tests it to false. (0 rows on the dev book, where all 376 accounts resolve a
    -- node; the hole is one unresolvable node wide, and it takes that account's positions,
    -- transactions and valuations with it.)
    o.lineage                                             AS lineage,
    m.name,
    m.aim,
    m.model_type,
    -- NOT the advised/discretionary flag, despite the name. Verified against staging:
    -- `investmentDecision` is the decision MAKER's identity — {"firmLei": ..., "personId":
    -- "JN019654C", "personIdType": "NINO"} — so publishing it as the canonical
    -- `investment_decision` would have put a JSON blob where the portals expect a word.
    -- Advised vs discretionary lives on the ACCOUNT's wrapperDetail (booleans `advised` and
    -- `discretionary`, which platform.accounts reads); for a model it is a Graphene-side
    -- classification the sync must leave alone.
    m.investment_decision                                   AS decision_maker,
    m.product_type                                          AS product_key,
    cast(m.risk_rating AS varchar)                          AS risk_rating,
    -- RATES ARE FRACTIONS. Seccl publishes `modelCharge` as `0.002` for 0.2% — the same
    -- convention fee_packages / allocations / cash_allocation already scale
    -- (`0.0035` = 0.35%). `platform.models.model_charge` is 0-100, matching `target_return`
    -- (6.5 = 6.5%) and CTC's `management_charge` ("% management charge"). Leaving it unscaled
    -- made the catalogue show 0.0020 under a (%) label and sent a hundredth of the charge into
    -- an illustration. DECIMAL before * 100, not float: `0.002::double * 100` is
    -- 0.20000000000000004.
    try_cast(try_cast(m.model_charge AS decimal(12, 8)) * 100 AS decimal(8, 4))
                                                            AS model_charge,
    try_cast(m.vatable AS boolean)                          AS vatable,
    try_cast(m.suspend_rebalances AS boolean)               AS suspend_rebalances,
    try_cast(m.cash_allocation AS decimal(9, 6))            AS cash_allocation,
    lower(coalesce(m.status, 'active'))                     AS status,
    m.status                                                AS status_raw,
    -- The version-shaped payload the materialiser expands. `allocations` is one entry per
    -- holding with its percentage; `asset_mix` is the instrument-type split Seccl derives from
    -- it; `commentary` is free text the firm wrote.
    m.allocations,
    m.asset_mix,
    m.commentary,
    cast(m.available_to_node_id AS varchar)                 AS available_to_key,
    m.available_to_node_name                                AS available_to_org_name,
    try_cast(m.audit_details ->> 'updateDate' AS timestamp) AS source_updated_at,
    coalesce(try_cast(m.audit_details ->> 'updateDate' AS date), current_date) AS as_of_date,
    to_json(m)                                              AS data
FROM raw.models AS m
LEFT JOIN platform.nodes AS o
    ON o.key = coalesce(try_cast(cast(m.node_id AS varchar) AS varchar[])[1],
                            cast(m.node_id AS varchar))
