/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.contact_details, platform.clients]
  tags: [sei, entity, conformed]
  description: >
    Addresses, phone numbers and email addresses per entity — the contactable detail
    platform.clients only carries one correspondence address of.
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- ONE ROW PER (entity, contact record). The report interleaves three kinds of contact point in
-- one row shape — an address block, a phone block and an email block, each with its own type,
-- primary flag and status — and any given row populates one of them. They are kept in one
-- table with a `contact_kind` discriminator rather than split into three, because that is the
-- grain SEI delivers and splitting would invent rows.
--
-- ORG COMES FROM THE CLIENT. This report carries no FIRM_ID at all, so the only way to scope a
-- contact point is through the entity it belongs to. An entity that is not a client in
-- platform.clients (an adviser, an organisation) resolves to no org and so fans out to nobody
-- — visible as a null key rather than silently attributed to the wrong firm.
WITH latest AS (SELECT max(dt) AS dt FROM raw.contact_details),
current AS (
    SELECT c.* FROM raw.contact_details c JOIN latest ON c.dt = latest.dt
    QUALIFY row_number() OVER (
        PARTITION BY c."ENTITY_IDENTIFIER", c."CONTACT_IDENTIFIER", c."CONTACT_TYPE"
        ORDER BY c."PRIMARY_ADDRESS_FLAG" DESC NULLS LAST
    ) = 1
)
SELECT
    -- The conformed spine (docs/platform-model.md §3), published alongside the tenancy
    -- columns rather than instead of them — see platform/accounts.sql for why the fan-out
    -- switch is a step of its own.
    concat_ws(':', c."ENTITY_IDENTIFIER", c."CONTACT_TYPE", c."CONTACT_IDENTIFIER")
                                                    AS external_id,
    cl.key,
    -- NOT `coalesce(cl.lineage, [])`, which is what this said and is the opposite of the
    -- header above: an EMPTY lineage is the fan-out's GLOBAL sentinel, so a contact point whose
    -- entity is not a client would have projected to EVERY tenant rather than to none. NULL
    -- is what "no org" spells (`book_sink.predicate.conformed` tests NULL to false).
    cl.lineage,
    NULL::timestamp                                 AS source_updated_at,
    c."ENTITY_IDENTIFIER"                           AS entity_external_id,
    c."CONTACT_IDENTIFIER"                          AS contact_id,
    c."CONTACT_TYPE"                                AS contact_type,
    -- Which block this row actually populated.
    CASE
        WHEN c."EMAIL_ADDRESS" IS NOT NULL THEN 'email'
        WHEN c."PHONE_NUMBER" IS NOT NULL OR c."PHONE_LOCAL_NUMBER" IS NOT NULL THEN 'phone'
        WHEN c."ADDRESS_LINE_1" IS NOT NULL THEN 'address'
    END                                             AS contact_kind,
    c."ENTITY_STATUS"                               AS entity_status,
    c."ADDRESS_TYPE"                                AS address_type,
    c."PRIMARY_ADDRESS_FLAG"                        AS is_primary_address,
    c."ADDRESS_STATUS"                              AS address_status,
    list_filter([c."ADDRESS_LINE_1", c."ADDRESS_LINE_2", c."ADDRESS_LINE_3",
                 c."ADDRESS_LINE_4", c."ADDRESS_LINE_5"],
                x -> x IS NOT NULL AND x <> '')     AS address_lines,
    c."ADDRESS_CITY"                                AS address_city,
    c."ADDRESS_POLITICAL_SUBDIVISION"               AS address_region,
    c."ADDRESS_POSTAL_CODE"                         AS address_postcode,
    c."ADDRESS_COUNTRY_CODE"                        AS address_country,
    c."PHONE_NUMBER_TYPE"                           AS phone_type,
    c."PRIMARY_PHONE_FLAG"                          AS is_primary_phone,
    c."PHONE_STATUS"                                AS phone_status,
    c."PHONE_NUMBER"                                AS phone_number,
    c."PHONE_NUMBER_COUNTRY_CODE"                   AS phone_country_code,
    c."PHONE_DESCRIPTION"                           AS phone_description,
    c."EMAIL_TYPE"                                  AS email_type,
    c."PRIMARY_EMAIL_FLAG"                          AS is_primary_email,
    c."EMAIL_STATUS"                                AS email_status,
    c."EMAIL_ADDRESS"                               AS email_address,
    cl.firm_id,
    cl.subfirm_id,
    c.dt                                            AS as_of_date,
    to_json(c)                                      AS data
FROM current c
LEFT JOIN platform.clients cl ON cl.client_id = c."ENTITY_IDENTIFIER"
