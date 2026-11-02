/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.contacts, raw.adviser_contacts]
  tags: [morgan_lloyd, entity, conformed]
  description: >
    CRM contacts, conformed — the nightly whole-book baseline plus any contact only visible
    through an adviser scope. Morgan Lloyd's equivalent of seccl.clients / sei.clients.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: key
*/

-- This was a bare `select *` view with no declared key. It is now a keyed table publishing the
-- conformed contract (docs/platform-model.md §3), which is what lets the materialiser read
-- Morgan Lloyd the same way it reads Seccl.
--
-- It is `clients`, not `contacts`, because §3 names the person entity `clients` and Seccl and
-- SEI both publish under that name. Publishing the right columns under the wrong name is the
-- same failure as the wrong columns: `app/materialise.py` asks each provider for `clients`, so
-- while this was `platform.contacts` every ML contact was a counted `unavailable` reject and no
-- morgan_lloyd row ever reached crm.contacts. The provider name is carried by the catalogue,
-- not by the relation.
--
-- Morgan Lloyd's org axis is a single Microsoft tenant — one Dynamics/Azure tenant per firm —
-- so key is that tenant id and lineage is the one-element path containing it. Unlike SEI
-- there is no hierarchy to walk: the firm IS the root and the leaf. Stamped on every row by
-- the extractors (`mlTenantId`), because a multi-firm deployment syncs each connection in turn
-- and a row has to say which connection it came from.
--
-- The adviser-book overlay is kept: `cam_getadvisercontacts` can return a contact the
-- whole-book list does not, so it is unioned in for ids the baseline lacks. It is scoped by
-- ML_SYNC_ADVISER_EMAILS and lands nothing when that is unset, so today it is a no-op — see
-- models/raw_odata.py for why the scope-bound extractors are the weak part of this sync.
WITH book AS (
    SELECT * FROM raw.contacts
    UNION ALL BY NAME
    SELECT a.* EXCLUDE (adviser_email)
    FROM raw.adviser_contacts AS a
    WHERE a.contactid NOT IN (SELECT contactid FROM raw.contacts)
),
current AS (
    SELECT * FROM book
    QUALIFY row_number() OVER (PARTITION BY contactid ORDER BY modifiedon DESC NULLS LAST) = 1
)
SELECT
    c.contactid                                     AS external_id,
    c.ml_tenant_id                                  AS key,
    [c.ml_tenant_id]                                AS lineage,
    -- Dataverse stamps every row, so unlike the file-drop providers this is a real
    -- provider-side modification time rather than the extract date.
    try_cast(c.modifiedon AS timestamp)             AS source_updated_at,
    -- A CRM contact is always a natural person here; organisations are `accounts`.
    'individual'                                    AS client_type,
    -- The book reads sub_type on every provider. A person has none.
    NULL::varchar                                   AS sub_type,
    c.firstname                                     AS first_name,
    c.middlename                                    AS middle_name,
    c.lastname                                      AS surname,
    c.fullname                                      AS full_name,
    try_cast(c.birthdate AS date)                   AS date_of_birth,
    c.emailaddress1                                 AS email,
    -- Two numbers, not a coalesce. `telephone1` is Dynamics' primary business phone and lands
    -- in `crm.contacts.phone`. Taking it as the mobile put a switchboard number in the column
    -- every surface reads as "the number to ring this client on". `telephone2` is not read:
    -- the book holds one other number.
    nullif(trim(c.mobilephone), '')                AS mobile,
    nullif(trim(c.telephone1), '')                 AS phone,
    -- ONE address struct with the same field names every provider uses (§3), not four flat
    -- columns. Seccl and SEI both publish this shape and the book stores one jsonb, so publishing
    -- it flat here meant the pipeline read `address` and found nothing — a Binder Error that
    -- aborts the tenant, recorded as "unavailable", which reads as "this firm has no data".
    -- `lines` is a LIST and not rows: it is one address.
    {
        'lines': list_filter([c.address1_line1, c.address1_line2],
                             x -> x IS NOT NULL AND x <> ''),
        'city': c.address1_city,
        'region': c.address1_stateorprovince,
        'postcode': c.address1_postalcode,
        'country': c.address1_country
    }                                               AS address,
    -- The flat columns stay published beside it: they are what the lake-side readers already
    -- select, and dropping them would be a second change riding along with this one.
    list_filter([c.address1_line1, c.address1_line2],
                x -> x IS NOT NULL AND x <> '')     AS address_lines,
    c.address1_city                                 AS address_city,
    c.address1_postalcode                           AS address_postcode,
    c.address1_country                              AS address_country,
    -- Dynamics' own display name is published as the entity name: `fullname` is what the CRM shows
    -- and what its users search. In the book, though, a person's `name` is their parts (0239), so
    -- it lands as first, middle and surname whenever both are held; `full_name` above keeps
    -- Dynamics' spelling for the lake-side readers. (Seccl reserves `entity_name` for
    -- non-individuals; ML's contacts are all natural persons.)
    c.fullname                                      AS entity_name,
    -- Published as explicit NULLs rather than omitted, which is what SEI's models do for the same
    -- reason: the conformed contract is a fixed column set, and a reader must not have to know
    -- which provider happens to carry which field.
    --
    -- These three DO exist on this org's contact, as the custom fields the create path already
    -- sends (`app/integrations/morgan_lloyd/contacts.py`: cam_title, cam_nationality, cam_ninumber).
    -- They are NULL here because reading them back is not a rename: `cam_title` is an option-set
    -- CODE and needs the reverse of `_title_code`, and none of the three has been verified against
    -- what the nightly OData pull actually lands. Guessing a mapping is how a client record ends up
    -- confidently wrong, so the column exists and waits for the lookup.
    NULL::varchar                                   AS title,
    NULL::varchar[]                                 AS nationality,
    NULL::varchar                                   AS national_identifier,
    NULL::varchar                                   AS national_identifier_type,
    -- The Clients-report facts SEI publishes. This org's contact may carry them as
    -- option-set codes; they are NULL until that map is verified against the pull.
    NULL::varchar                                   AS gender,
    NULL::varchar                                   AS marital_status,
    NULL::boolean                                   AS is_vulnerable,
    NULL::varchar                                   AS country_of_birth,
    NULL::varchar                                   AS fatca_status,
    NULL::boolean                                   AS us_person,
    NULL::varchar                                   AS passport_number,
    NULL::varchar                                   AS passport_country,
    -- Morgan Lloyd books are sterling. A literal, so the conformed column set stays fixed.
    'GBP'                                           AS base_currency,
    -- Dataverse statecode: 0 active, 1 inactive. Mapped to the canonical vocabulary, with the
    -- raw value kept so an unmapped state is visible rather than silently NULL.
    CASE try_cast(c.statecode AS integer) WHEN 0 THEN 'active' WHEN 1 THEN 'inactive' END
                                                    AS status,
    c.statecode                                     AS status_raw,
    try_cast(c.createdon AS timestamp)              AS created_at,
    c.ml_tenant_id,
    current_date                                    AS as_of_date,
    to_json(c)                                      AS data
FROM current c
