/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: ref.transfer_providers
  tags: [sei, entity, conformed]
  description: >
    Ceding counterparties from the Reference report, in the shape the book
    register reads. Global: firm_id is null on every code.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: name
*/

-- One organisation, one code. The same code is listed under more than one kind
-- (transfer agent, custodian, cash agent) for 44 names, and the name agrees every
-- time. The code is the counterparty id a transfer-in sends, so the row is keyed
-- on it and the kinds travel in `data`. Origo and ATG are not in this file:
-- `rails`, `origo_id` and `atg_reference` stay empty.
--
-- `min` / `list_sort` rather than `any_value`: full_merge reads this query twice,
-- and a tie that picks a different name on the second read fails `unique: external_id`.
WITH grouped AS (
    SELECT
        provider_id                                          AS provider_id,
        min(provider_name)                                   AS provider_name,
        min(country_code)                                    AS country_code,
        max(as_of_date)                                      AS as_of_date,
        list_sort(list_distinct(list(kind)))                 AS kinds
    FROM ref.transfer_providers
    WHERE provider_id IS NOT NULL
      AND nullif(trim(provider_name), '') IS NOT NULL
    GROUP BY provider_id
)
SELECT
    provider_id                                              AS external_id,
    ''                                                       AS key,
    -- Global reference data. `predicate.sei` projects this table whole.
    []::varchar[]                                            AS lineage,
    provider_name                                            AS name,
    'active'                                                 AS status,
    true                                                     AS is_active,
    []::STRUCT(rail VARCHAR, reference VARCHAR, products VARCHAR[])[] AS rails,
    []::VARCHAR[]                                            AS products,
    NULL::varchar                                            AS origo_id,
    NULL::varchar                                            AS atg_reference,
    as_of_date::timestamp                                    AS source_updated_at,
    as_of_date,
    to_json({'kinds': kinds, 'country_code': country_code})  AS data
FROM grouped
