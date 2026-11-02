/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.relationships, platform.clients]
  tags: [sei, entity, conformed]
  description: >
    Entity-to-entity relationships — beneficial owners, trustees, guardians, authorised
    representatives. The AML beneficial-ownership question is answered from this.
  checks:
    - not_null: external_id
    - unique: external_id
*/

-- ORG COMES FROM THE PRIMARY ENTITY, for the same reason as contact_points: the report carries
-- no FIRM_ID. A relationship whose primary entity is not a client resolves to no org.
--
-- The secondary entity's own details (name, DOB, tax ids, address, citizenship) are carried on
-- the relationship row rather than looked up, because SEI sends them here and the secondary
-- party is frequently NOT a client in its own right — a beneficial owner with no account
-- exists only on this report.
WITH latest AS (SELECT max(dt) AS dt FROM raw.relationships),
current AS (
    SELECT r.* FROM raw.relationships r JOIN latest ON r.dt = latest.dt
    QUALIFY row_number() OVER (PARTITION BY r."RELATIONSHIP_IDENTIFIER"
                               ORDER BY r."RELATIONSHIP_CREATE_DATE" DESC NULLS LAST) = 1
)
SELECT
    -- The conformed spine (docs/platform-model.md §3), published alongside the tenancy
    -- columns rather than instead of them — see platform/accounts.sql for why the fan-out
    -- switch is a step of its own.
    r."RELATIONSHIP_IDENTIFIER"                     AS external_id,
    cl.key,
    -- NOT `coalesce(cl.lineage, [])`, which is what this said and is the opposite of the
    -- header above: an EMPTY lineage is the fan-out's GLOBAL sentinel, so a relationship whose
    -- entity is not a client would have projected to EVERY tenant rather than to none. NULL
    -- is what "no org" spells (`book_sink.predicate.conformed` tests NULL to false).
    cl.lineage,
    NULL::timestamp                                 AS source_updated_at,
    r."PRIMARY_ENTITY_IDENTIFIER"                   AS primary_entity_external_id,
    r."PRIMARY_ENTITY_TYPE"                         AS primary_entity_type,
    r."PRIMARY_ENTITY_DESCRIPTION"                  AS primary_entity_description,
    r."RELATIONSHIP_TYPE"                           AS relationship_type,
    r."RELATIONSHIP_ROLE"                           AS relationship_role,
    r."RELATIONSHIP_CREATE_DATE"                    AS created_on,
    r."SECONDARY_ENTITY_IDENTIFIER"                 AS secondary_entity_external_id,
    r."SECONDARY_ENTITY_TYPE"                       AS secondary_entity_type,
    r."SECONDARY_ENTITY_NAME"                       AS secondary_entity_name,
    r."SECONDARY_ENTITY_FIRST_NAME"                 AS secondary_entity_first_name,
    r."SECONDARY_ENTITY_MIDDLE_NAME"                AS secondary_entity_middle_name,
    r."SECONDARY_ENTITY_TITLE"                      AS secondary_entity_title,
    r."SECONDARY_ENTITY_DATE_OF_BIRTH"              AS secondary_entity_date_of_birth,
    r."SECONDARY_ENTITY_DECEASED_DATE"              AS secondary_entity_deceased_on,
    r."SECONDARY_ENTITY_CITIZENSHIP_COUNTRY"        AS secondary_entity_citizenship,
    r."SECONDARY_ENTITY_COUNTRY_OF_DOMICILE"        AS secondary_entity_domicile,
    r."SECONDARY_ENTITY_ORGANIZATION_TYPE"          AS secondary_entity_organisation_type,
    r."SECONDARY_ENTITY_PRIMARY_TAX_TYPE"           AS secondary_entity_tax_type,
    r."SECONDARY_ENTITY_PRIMARY_TAX_ID"             AS secondary_entity_tax_id,
    list_filter([r."SECONDARY_ADDRESS_LINE_1", r."SECONDARY_ADDRESS_LINE_2",
                 r."SECONDARY_ADDRESS_LINE_3", r."SECONDARY_ADDRESS_LINE_4",
                 r."SECONDARY_ADDRESS_LINE_5"],
                x -> x IS NOT NULL AND x <> '')     AS secondary_address_lines,
    r."SECONDARY_ADDRESS_CITY"                      AS secondary_address_city,
    r."SECONDARY_ADDRESS_POSTAL_CODE"               AS secondary_address_postcode,
    r."SECONDARY_ADDRESS_COUNTRY"                   AS secondary_address_country,
    -- The AML-relevant pair: who owns how much of the primary entity, and why.
    r."BENEFICIAL_OWNERSHIP_PERCENTAGE"             AS beneficial_ownership_percentage,
    r."BENEFICIAL_OWNERSHIP_NOTES"                  AS beneficial_ownership_notes,
    r."GUARDIAN_OF_THE_PERSON"                      AS guardian_of_the_person,
    r."LEGAL_ENTITY_IDENTIFIER"                     AS legal_entity_identifier,
    cl.firm_id,
    cl.subfirm_id,
    r.dt                                            AS as_of_date,
    to_json(r)                                      AS data
FROM current r
LEFT JOIN platform.clients cl ON cl.client_id = r."PRIMARY_ENTITY_IDENTIFIER"
