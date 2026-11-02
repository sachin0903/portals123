/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.clients, platform.nodes]
  tags: [seccl, entity, conformed]
  description: >
    Clients, conformed — the nightly baseline overlaid with realtime webhook deltas.
    One row per client, carrying the key/lineage pair every conformed entity
    publishes (docs/platform-model.md §3).
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- Previously a keyless view. It is now a keyed table because the materialiser upserts from it:
-- you cannot upsert safely from a relation whose grain is undeclared.
--
-- NO WEBHOOK OVERLAY. The staging tables are event ENVELOPES, not records: verified against
-- staging, `staging.positions` carries only (_event_key, firm_id, id, account_id, position_type,
-- update_date, _offset, _ingested_at), `staging.clients` only (client_id, update_date) and
-- `staging.transactions` only (id, transaction_type, status, update_date). The previous
-- `union all by name` over the baseline therefore did the opposite of what it looked like: a
-- record with a webhook event was EXCLUDED from the baseline by the NOT EXISTS and replaced by
-- a stub whose every other column was NULL. It is latent today only because the streams are
-- empty. The stream is a change SIGNAL — what it should drive is a targeted re-fetch of the
-- changed id, not a substitution — so until that exists the baseline is the record.
WITH current_clients AS (
    SELECT * FROM raw.clients
)
-- node_id is scalar-or-list: Seccl sends `["9"]` on clients and `41` on positions (the
-- quirk app/platforms.py::_SLICE and fanout._predicate both handle). Taking the first
-- element of the list form, or the scalar as-is, is what makes key one value.
SELECT
    cast(c.id AS varchar)                                   AS external_id,
    coalesce(try_cast(cast(c.node_id AS varchar) AS varchar[])[1], cast(c.node_id AS varchar))
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
    -- Seccl says 'Individual'; anything else is an entity of some kind (corporate,
    -- trust). The canonical two-value split is crm.contact_type, and the provider's own
    -- word survives in client_type_raw so a new value degrades to 'entity' rather than
    -- failing the sync.
    CASE WHEN lower(c.client_type) = 'individual' THEN 'individual' ELSE 'entity' END
                                                            AS client_type,
    c.client_type                                           AS client_type_raw,
    -- trust | company | charity, and only when Seccl names that kind. Any other
    -- non-individual stays a plain organisation. A person has no sub-type.
    CASE lower(c.client_type)
        WHEN 'trust' THEN 'trust'
        WHEN 'charity' THEN 'charity'
        WHEN 'company' THEN 'company'
        WHEN 'corporate' THEN 'company'
    END                                                     AS sub_type,
    c.first_name,
    c.middle_name,
    c.surname,
    c.entity_name,
    c.title,
    try_cast(c.date_of_birth AS date)                       AS date_of_birth,
    -- `nationalities` is a list of objects keyed by ISO code; `nationality` is the
    -- scalar legacy field. Both are published by Seccl and they disagree on some
    -- records, so the list wins and the scalar backfills.
    --
    -- A LIST, and it stays one: `crm.contacts.nationality` is `text[]` because a person can hold
    -- two, which is the same reason Seccl publishes two sources to reconcile. Resolving it to one
    -- value here was tried and reverted — it made the conformed column a scalar while the book's is
    -- an array, so the merge cast 'GB' to text[] and Postgres answered "malformed array literal".
    coalesce(
        [x ->> 'code' FOR x IN coalesce(try_cast(c.nationalities AS json[]), [])],
        list_filter([c.nationality], x -> x IS NOT NULL)
    )                                                       AS nationality,
    NULL::varchar                                           AS country_of_residence,
    c.tax_domicile                                          AS country_of_domicile,
    c.national_insurance_no                                 AS national_identifier,
    CASE WHEN c.national_insurance_no IS NOT NULL THEN 'nino' END
                                                            AS national_identifier_type,
    -- SEI publishes these from the Clients report. Seccl's payload does not, and a
    -- missing column aborts the tenant, so each is an explicit NULL.
    NULL::varchar                                           AS gender,
    NULL::varchar                                           AS marital_status,
    NULL::boolean                                           AS is_vulnerable,
    NULL::varchar                                           AS country_of_birth,
    NULL::varchar                                           AS fatca_status,
    NULL::boolean                                           AS us_person,
    NULL::varchar                                           AS passport_number,
    NULL::varchar                                           AS passport_country,
    c.email,
    -- Seccl publishes each number as an OBJECT -- {"number", "isMobile", "locale"} -- not as a
    -- string, so both need the same `->>` every other nested field in this model uses. Without
    -- it the whole object was cast to text by the sink and landed in a text column, and a
    -- client's record displayed the JSON where its number should be.
    --
    -- They stay two columns. Coalescing the landline into the mobile blanked the distinction
    -- the book now stores: a client Seccl holds only a landline for has `phone` set and
    -- `mobile` null. BOTH are safe to read only because `_CLIENT_SPINE` declares them
    -- (models/raw_core.py). `phone` is rarely populated: no client in this firm's book carries
    -- one, and none of the 205 example bodies in the vendored Postman collection does either.
    -- `records_to_table` infers columns from the records it is handed, so an unpopulated field
    -- is not a column, and reading it bound happily against the test stub and answered
    -- `Binder Error: Values list "c" does not have a column named "phone"` against the
    -- warehouse -- which aborts the entity.
    nullif(trim(c.mobile ->> 'number'), '')                 AS mobile,
    nullif(trim(c.phone ->> 'number'), '')                  AS phone,
    -- One address struct with the same field names for every provider. Seccl splits the
    -- street over flat/building/address1..4; SEI ships CORRESPONDENCE_ADDRESS_LINE1..3.
    -- Both collapse to `lines` -- a list, not rows: it is one address.
    {
        'lines': list_filter([
            c.address_detail ->> 'flatNumber',
            c.address_detail ->> 'buildingName',
            c.address_detail ->> 'buildingNumber',
            c.address_detail ->> 'address1',
            c.address_detail ->> 'address2',
            c.address_detail ->> 'address3',
            c.address_detail ->> 'address4'
        ], x -> x IS NOT NULL AND x <> ''),
        'city': NULL,
        'region': NULL,
        'postcode': c.address_detail ->> 'postCode',
        'country': c.address_detail ->> 'country'
    }                                                       AS address,
    -- Seccl books are sterling. Published as a literal so the conformed column set stays
    -- fixed: omitting it is a Binder Error that files the tenant as "unavailable".
    'GBP'                                                   AS base_currency,
    lower(c.status)                                         AS status,
    c.status                                                AS status_raw,
    try_cast(c.record_creation_date AS date)                AS opened_on,
    try_cast(c.audit_details ->> 'updateDate' AS timestamp) AS source_updated_at,
    coalesce(try_cast(c.audit_details ->> 'updateDate' AS date), current_date) AS as_of_date,
    to_json(c)                                              AS data
FROM current_clients AS c
LEFT JOIN platform.nodes AS o
    ON o.key = coalesce(try_cast(cast(c.node_id AS varchar) AS varchar[])[1], cast(c.node_id AS varchar))
