/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.master_parties]
  tags: [seccl, entity, conformed]
  description: >
    Schemes a transfer can be addressed to, in either direction: ceding providers to
    transfer FROM (with the rails each is reachable on -- Origo, ATG -- and the products
    each rail supports) and receiving providers to transfer TO.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: name
    # `rails` is empty, not null, for a provider that only receives -- see the note on
    # DIRECTION below. A row must do at least one of the two to be here at all.
    - not_null: rails
*/

-- THE FILTER IS `transfer_providers IS NOT NULL`, and getting this wrong was wrong twice over.
-- Verified against staging: 1,612 master parties, of which 1,529 carry an Origo Provider ID but
-- only 661 actually support a transfer. Filtering on "has an Origo id" therefore published 929
-- counterparties that cannot receive a transfer instruction at all, AND dropped the 5 that can
-- but are reachable only on ATG (Ascentric, P1 Investment Management and friends, whose
-- externalIds hold an "ATG Reference" and no Origo id). The register is defined by whether
-- Seccl publishes transfer rails for the party, not by which identifier it happens to hold.
--
-- SHAPE. `transferProviders` is a list of RAILS, each with its own reference and its own set of
-- supported products as dynamic object keys:
--   [{transferProviderName: 'Origo', providerReference: '3526', PENSION: {cashSupported: …}},
--    {transferProviderName: 'ATG', providerReference: 'LAWHGB21XXX', PENSION: …, GIA: …, ISA: …}]
-- So the product list is the object's keys minus the two scalar fields -- there is no fixed
-- column set to read, and a new wrapper appears as a new key rather than a new field.
-- DIRECTION. Seccl exposes the two directions as two fields on the SAME master party, and a
-- /masterparty/transferoutprovider endpoint that is a strict subset of this one -- verified on
-- staging: 1,612 parties, 661 with `transferProviders` (IN), 7 with `transferOutProvider` (OUT),
-- and that endpoint returns 5, being the 7 less the two whose cashSupported is false. So both
-- directions are read from master_parties and there is no second call. A provider may do
-- either, both, or (the other 951) neither.
WITH parties AS (
    SELECT *
    FROM raw.master_parties
    WHERE name IS NOT NULL
      AND (transfer_providers IS NOT NULL OR transfer_out_provider IS NOT NULL)
),

rails AS (
    SELECT
        cast(m.id AS varchar)                     AS external_id,
        m.name,
        m.status,
        m.external_ids,
        m.audit_details,
        m.wrapper_types_provided,
        rail ->> 'transferProviderName'            AS rail_name,
        rail ->> 'providerReference'               AS rail_reference,
        -- Everything that is not one of the two scalars is a product the rail supports.
        list_filter(
            json_keys(rail),
            k -> k NOT IN ('transferProviderName', 'providerReference')
        )                                          AS rail_products
    FROM parties AS m,
        unnest(coalesce(try_cast(m.transfer_providers AS json[]), [])) AS t (rail)
    WHERE m.transfer_providers IS NOT NULL
),

grouped AS (
    SELECT
        external_id,
        any_value(name)                            AS name,
        any_value(status)                          AS status,
        any_value(external_ids)                    AS external_ids,
        any_value(audit_details)                   AS audit_details,
        any_value(wrapper_types_provided)          AS wrapper_types_provided,
        -- One entry per rail, ordered so Origo leads: it is the electronic rail most transfers
        -- go out on, and a stable order keeps the UI's badges from reshuffling between runs.
        list(
            {
                'rail': rail_name,
                'reference': rail_reference,
                'products': rail_products
            }
            ORDER BY CASE WHEN rail_name = 'Origo' THEN 0 ELSE 1 END, rail_name
        )                                          AS rails,
        -- The union across rails, deduplicated: "which products can this provider transfer"
        -- is the question a firm asks first, before caring which rail carries them.
        list_sort(list_distinct(flatten(list(rail_products)))) AS products
    FROM rails
    GROUP BY external_id
)

SELECT
    cast(p.id AS varchar)                          AS external_id,
    -- Seccl-wide reference data (every row carries firm_id 'SECCL'), so no org scoping: an empty
    -- lineage is what admits a row to every tenant, the treatment instruments get.
    NULL::varchar                                  AS key,
    []::varchar[]                                  AS lineage,
    p.name,
    p.status,
    lower(coalesce(p.status, '')) = 'active'        AS is_active,
    -- Empty, not null, for a receive-only provider: "this provider has no inbound rails" is a
    -- fact, and a null would make every consumer coalesce it.
    coalesce(g.rails, []::struct(rail varchar, reference varchar, products varchar[])[]) AS rails,
    coalesce(g.products, []::varchar[])            AS products,
    -- The two directions, as flags a caller can filter on without knowing Seccl's field names.
    g.external_id IS NOT NULL                      AS transfers_in,
    p.transfer_out_provider IS NOT NULL            AS transfers_out,
    -- What a transfer OUT to this provider may carry. Null when it does not receive at all.
    try_cast(p.transfer_out_provider ->> 'cashSupported' AS boolean)     AS out_cash_supported,
    try_cast(p.transfer_out_provider ->> 'inSpecieSupported' AS boolean) AS out_in_specie_supported,
    -- Both identifier types, neither required. A provider reachable only on ATG has no Origo id
    -- and is still a provider.
    (
        SELECT x ->> 'value' FROM unnest(coalesce(try_cast(p.external_ids AS json[]), [])) AS t (x)
        WHERE x ->> 'idType' = 'Origo Provider ID' LIMIT 1
    )                                              AS origo_id,
    (
        SELECT x ->> 'value' FROM unnest(coalesce(try_cast(p.external_ids AS json[]), [])) AS t (x)
        WHERE x ->> 'idType' = 'ATG Reference' LIMIT 1
    )                                              AS atg_reference,
    p.wrapper_types_provided,
    try_cast(p.audit_details ->> 'updateDate' AS timestamp) AS source_updated_at,
    coalesce(try_cast(p.audit_details ->> 'updateDate' AS date), current_date) AS as_of_date,
    to_json(p)                                     AS data
FROM parties AS p
LEFT JOIN grouped AS g ON g.external_id = cast(p.id AS varchar)
