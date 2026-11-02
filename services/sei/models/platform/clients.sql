/*
interlace:
  materialise: virtual
  strategy: full_merge
  key: external_id
  depends_on: [raw.clients, platform.dsr_grants, raw.contact_details, platform.tenancy, ref.codes]
  tags: [sei, entity, conformed]
  description: >
    Clients (natural persons and entities), matching Seccl's entity of the same name.
    Address lines are collapsed to a list rather than kept as numbered columns.
  checks:
    - not_null: external_id
    - unique: external_id
    - not_null: key
    - not_null: firm_id
*/
-- CORRESPONDENCE_ADDRESS_LINE1..3 is NOT a repeating group — it is one address
-- split over numbered lines, so it collapses to a list, not to rows. Unpivoting it
-- would invent three addresses where there is one.
--
-- TENANCY. A client's firm comes from its own row, not only from its DSR grants.
-- Taking it from the grants alone left 786 of 2,774 clients (28%) with a NULL firm_id
-- — every one of which carries a FIRM_ID in the Clients report — and a client with no
-- firm fans out to nobody, so those 786 were invisible to their own tenant.
-- The grants still win where present: they are the more specific statement, and
-- subfirm has two spellings (the grant's, and RESIDING_SUBFIRM_ID on the client).
--
-- GRAIN. The Clients report is NOT one row per client: the latest drop carries 2,774
-- rows for 824 clients, because it repeats the whole client block once per
-- ADDRESS_PURPOSE (Pay To, Tax Reporting Recipient, Approved Statement Recipient, …
-- see ref.codes). This model publishes ONE ROW PER CLIENT, which is what
-- `key: client_id` and every downstream join assume — before this it emitted all
-- 2,774, so any join to it multiplied rows by ~3.4 and `key` was a false claim.
--
-- The purposes themselves are kept as a list, so collapsing loses nothing about them.
-- Where a client genuinely has more than one ADDRESS (5 of 824 do) the pick is the
-- primary address, then the most recently updated, then the lowest id — deterministic,
-- so a rebuild cannot quietly swap which address represents a client. Multi-address
-- and per-purpose detail want their own model rather than multiplying this one, as
-- does STOCK_AND_SHARE_ISA_ACCOUNT_NUMBER (varies within 30 clients — it is an
-- account-level field that the client report happens to carry).
with latest as (select max(dt) as dt from raw.clients),
purposes as (
  select c."CLIENT_ID" as client_id,
         list_sort(list_distinct(list(c."ADDRESS_PURPOSE"))) as address_purposes
  from raw.clients c join latest on c.dt = latest.dt
  group by c."CLIENT_ID"
),
current as (
  select c.* from raw.clients c join latest on c.dt = latest.dt
  qualify row_number() over (
    partition by c."CLIENT_ID"
    order by c."ADDRESS_PRIMARY_FLAG" desc nulls last,
             c."ADDRESS_LAST_UPDATED" desc nulls last,
             c."ADDRESS_ID"
  ) = 1
),
grants as (
  select subject_id as client_id,
         list_sort(list_distinct(list(dsr_id))) as dsr_team_ids,
         list_sort(list_distinct(list(dsr_id) filter (where identifies_firm)))
           as adviser_firm_dsr_ids,
         any_value(firm_id) as firm_id,
         any_value(subfirm_id) as subfirm_id
  from platform.dsr_grants where scope = 'client' and grantee = 'team' group by subject_id
)
-- The client's own contact details, one row per client, read from the RAW report rather than from
-- platform.contact_points: that model joins platform.clients for its org (its report carries no
-- FIRM_ID at all), so reading it back here is a dependency cycle — which interlace refuses outright,
-- and rightly.
--
-- `any_value FILTER` picks the primary that SEI itself flagged, so choosing is not a judgement this
-- model has to make. The coalesce behind it takes any record when nothing is flagged, because an
-- email is more use than a NULL and the fallback is visible here rather than inferred later.
, contacts as (
    select c."ENTITY_IDENTIFIER" as entity_external_id,
           coalesce(any_value(c."EMAIL_ADDRESS") filter (where c."PRIMARY_EMAIL_FLAG"),
                    any_value(c."EMAIL_ADDRESS"))            as email,
           coalesce(any_value(c."PHONE_NUMBER") filter (where c."PRIMARY_PHONE_FLAG"),
                    any_value(c."PHONE_NUMBER"))             as primary_phone,
           coalesce(any_value(address_lines) filter (where c."PRIMARY_ADDRESS_FLAG"),
                    any_value(address_lines))                as address_lines,
           coalesce(any_value(c."ADDRESS_CITY") filter (where c."PRIMARY_ADDRESS_FLAG"),
                    any_value(c."ADDRESS_CITY"))             as address_city,
           coalesce(any_value(c."ADDRESS_POLITICAL_SUBDIVISION")
                        filter (where c."PRIMARY_ADDRESS_FLAG"),
                    any_value(c."ADDRESS_POLITICAL_SUBDIVISION")) as address_region,
           coalesce(any_value(c."ADDRESS_POSTAL_CODE") filter (where c."PRIMARY_ADDRESS_FLAG"),
                    any_value(c."ADDRESS_POSTAL_CODE"))      as address_postcode,
           coalesce(any_value(c."ADDRESS_COUNTRY_CODE") filter (where c."PRIMARY_ADDRESS_FLAG"),
                    any_value(c."ADDRESS_COUNTRY_CODE"))     as address_country
      from (
        -- The latest drop only, the same window every other model takes.
        select c.*,
               list_filter([c."ADDRESS_LINE_1", c."ADDRESS_LINE_2", c."ADDRESS_LINE_3",
                            c."ADDRESS_LINE_4", c."ADDRESS_LINE_5"],
                           x -> x is not null and x <> '') as address_lines
          from raw.contact_details c
          join (select max(dt) as dt from raw.contact_details) latest on c.dt = latest.dt
      ) c
     group by c."ENTITY_IDENTIFIER"
)
-- Mobile and the other number, from the same drop. A row whose type says mobile is the
-- mobile; otherwise the primary (or any non-fax) number is, which is what the one-phone
-- model already stored. A second number that is not that mobile, and not a fax or a pager,
-- is `phone`. PHONE_NUMBER_TYPE is free text on the drop, so the match is the word, not a code.
, phone_rows as (
    select c."ENTITY_IDENTIFIER" as entity_external_id,
           nullif(trim(c."PHONE_NUMBER"), '') as number,
           c."PRIMARY_PHONE_FLAG" as is_primary,
           lower(coalesce(c."PHONE_NUMBER_TYPE", '')) as phone_type
      from raw.contact_details c
      join (select max(dt) as dt from raw.contact_details) latest on c.dt = latest.dt
     where nullif(trim(c."PHONE_NUMBER"), '') is not null
)
, phones as (
    select entity_external_id,
           coalesce(
             arg_min(number, case when is_primary then 0 else 1 end)
               filter (where phone_type like '%mobile%'),
             arg_min(number, case when is_primary then 0 else 1 end)
               filter (where phone_type not like '%fax%' and phone_type not like '%pager%')
           ) as mobile,
           arg_min(number, case when is_primary then 1 else 0 end)
             filter (
               where phone_type not like '%mobile%'
                 and phone_type not like '%fax%'
                 and phone_type not like '%pager%'
             ) as other_number
      from phone_rows
     group by entity_external_id
)
-- THE SUBFIRM IS THE ACCOUNT'S. `RESIDING_SUBFIRM_ID` is blank on every row of the Clients
-- report (firm 3106: 429 clients, 0 with a subfirm), so a slice that filters
-- `subfirm_id IN (...)` publishes nobody. `platform.tenancy` already resolved each account's
-- subfirm; a client is in a subfirm when they hold an account there. A list, because one client
-- holds accounts at two. This model must not read `platform.accounts`: that model joins
-- `platform.clients` for the allowance, and the cycle is one interlace refuses.
, account_subfirms as (
    select client_id,
           list_sort(list_distinct(list(subfirm_id) filter (where subfirm_id is not null)))
             as subfirm_ids
      from (
        select unnest(t.client_ids) as client_id, t.subfirm_id
          from platform.tenancy t
      )
     group by client_id
)
-- TITLE is a Reference code, the same shape as GENDER. The drop carries the number
-- (1 is Mr, 2 is Mrs, 0 is unset). One description per code: two firms spelling one
-- code differently would be a guess, so that code publishes nothing.
, titles as (
    select code,
           case when count(distinct description) = 1 then max(description) end as description
      from ref.codes
     where type = 'TITLE'
     group by code
)
-- The handful of descriptions that are the same title as a shorter form already
-- on the record. Mr. and Mr are one option; Professor and Prof are one option.
, title_aliases (source, canonical) as (
    values
      ('Mr.', 'Mr'),
      ('Mrs.', 'Mrs'),
      ('Ms.', 'Ms'),
      ('Dr.', 'Dr'),
      ('Mx.', 'Mx'),
      ('Professor', 'Prof'),
      ('Captain', 'Capt'),
      ('The Reverend', 'Reverend'),
      ('The Honorable', 'The Honourable'),
      ('H E Shaikh', 'H E Sheikh')
)

select
    -- The conformed spine (docs/platform-model.md §3), published alongside the existing
    -- tenancy columns rather than instead of them — see platform/accounts.sql for why the
    -- fan-out switch is a separate step.
  c."CLIENT_ID"                                     as external_id,
  coalesce(g.subfirm_id, c."RESIDING_SUBFIRM_ID", g.firm_id, c."FIRM_ID") as key,
  -- `nullif(…, [])` because an EMPTY lineage is the fan-out's GLOBAL sentinel — see
  -- platform/accounts.sql. This model is the org of record for the CLIENT axis the way
  -- accounts is for the account one: platform.{contact_points, relationships} select it.
  nullif(list_distinct(list_filter(
      list_concat([coalesce(g.firm_id, c."FIRM_ID"),
                   coalesce(g.subfirm_id, c."RESIDING_SUBFIRM_ID")],
                  coalesce(g.dsr_team_ids, [])),
      x -> x is not null
  )), []::VARCHAR[])                                as lineage,
  null::timestamp                                   as source_updated_at,
  to_json(c)                                        as data,
  c."CLIENT_ID"                                     as client_id,
  -- CLIENT_TYPE is a Reference code, not a word: 5 Individual, 6 Organization.
  -- The conformed vocabulary is individual | entity. A code this drop has not
  -- seen is an entity, which the book stores as a plain organisation.
  -- The word is accepted too, because a fixture and an older extract spell it out.
  case
    when lower(trim(c."CLIENT_TYPE")) in ('5', 'individual') then 'individual'
    else 'entity'
  end                                               as client_type,
  c."CLIENT_TYPE"                                   as client_type_raw,
  -- ORGANIZATION_TYPE, and only the three kinds the book stores. 14 Trust and
  -- 10 Charitable Remainder Trust are trusts; 2 is Charity; 4 Corporations and
  -- 11 Limited Liability Company are companies. Pension, partnership, estate,
  -- endowment and Other stay a plain organisation. A person has no sub-type.
  case
    when lower(trim(c."CLIENT_TYPE")) in ('5', 'individual') then null
    when c."ORGANIZATION_TYPE" in ('14', '10') then 'trust'
    when c."ORGANIZATION_TYPE" = '2' then 'charity'
    when c."ORGANIZATION_TYPE" in ('4', '11') then 'company'
  end                                               as sub_type,
  c."FIRST_NAME"                                    as first_name,
  c."ACCOUNT_HOLDERS_SURNAME"                       as surname,
  -- A number this reference does not name stays NULL. Publishing "1" is what put a
  -- digit in the title field. A title the extract already spells out is kept, and
  -- run through the same aliases, so an older file that says "Mr." still lands as "Mr".
  case
    when c."TITLE" is null or trim(c."TITLE") in ('', '0') then null
    when try_cast(trim(c."TITLE") as bigint) is not null
     and trim(c."TITLE") = (try_cast(trim(c."TITLE") as bigint))::varchar
      then coalesce(ad.canonical, t.description)
    else coalesce(aw.canonical, trim(c."TITLE"))
  end                                               as title,
  -- Dates arrive typed from raw.clients (sei_sync.schema); cast here they were NULL
  -- for every row, SEI's 19650306 being unreachable by CAST.
  c."DATE_OF_BIRTH"                                 as date_of_birth,
  -- A LIST, matching the conformed shape and the book's own `text[]`: SEI states one nationality,
  -- so it is a one-element list rather than a scalar. Published as a list because the column a
  -- reader lands it in holds several, not because SEI has several.
  list_filter([c."NATIONALITY"], x -> x is not null and x <> '')
                                                    as nationality,
  c."COUNTRY_OF_RESIDENCE"                          as country_of_residence,
  c."COUNTRY_OF_DOMICILE"                           as country_of_domicile,
  -- NATIONAL_INSURANCE_NUMBER is empty on every profiled drop. The number SEI actually
  -- stores is TAX_ID_NUMBER, and TAX_ID_TYPE says what it is (ref.codes, type TAX_ID_TYPE):
  -- 7 is National Insurance Number. 36 is "No Tax ID on File" — those rows still carry a
  -- number, and it is a placeholder (one value shared by a whole firm), so it is not a NINO.
  coalesce(
    nullif(trim(c."NATIONAL_INSURANCE_NUMBER"), ''),
    case when c."TAX_ID_TYPE" = '7' then nullif(trim(c."TAX_ID_NUMBER"), '') end
  )                                                 as national_insurance_number,
  -- THE CONFORMED CONTRACT (docs/platform-model.md §3). `pkgs/book-sink` projects one shape for
  -- every provider, so a column it reads and this model does not publish is a Binder Error that
  -- aborts the whole tenant and is recorded as "unavailable" — indistinguishable from "this firm has
  -- no data". Each of these is either the same value under the canonical name, or an explicit NULL
  -- where SEI genuinely does not carry it.
  coalesce(
    nullif(trim(c."NATIONAL_INSURANCE_NUMBER"), ''),
    case when c."TAX_ID_TYPE" = '7' then nullif(trim(c."TAX_ID_NUMBER"), '') end
  )                                                 as national_identifier,
  -- The type, because the book stores both and 'nino' is the only kind SEI publishes. NULL rather
  -- than 'nino' when the number is absent, so the pair cannot say "we hold a NINO" and hold none.
  case
    when coalesce(
           nullif(trim(c."NATIONAL_INSURANCE_NUMBER"), ''),
           case when c."TAX_ID_TYPE" = '7' then nullif(trim(c."TAX_ID_NUMBER"), '') end
         ) is not null
    then 'nino'
  end                                               as national_identifier_type,
  -- GENDER is 1 Male / 2 Female. 0 is unset, and it is the common value, so it is
  -- not a gender. Anything else is left NULL rather than a word the book will reject.
  case c."GENDER" when '1' then 'male' when '2' then 'female' end
                                                    as gender,
  -- MARITAL_STATUS. 0 is unset. Separated (3) and Partnered (7) have no book value:
  -- partnered is not specifically a civil partnership or cohabiting.
  case c."MARITAL_STATUS"
    when '1' then 'married'
    when '2' then 'single'
    when '8' then 'divorced'
    when '9' then 'widowed'
    when '10' then 'widowed'
  end                                               as marital_status,
  -- 1 Yes / 2 No, the same pair SEI uses on its other yes/no flags. 2 is almost
  -- every row. NULL when the report said nothing, so a blank is not "not vulnerable".
  case c."VULNERABLE_FLG" when '1' then true when '2' then false end
                                                    as is_vulnerable,
  -- Already an ISO code (GB, US, JE). A name or a longer token is not stored:
  -- the column holds the code the fact-find now stores.
  case when regexp_matches(upper(trim(c."COUNTRY_OF_BIRTH")), '^[A-Z]{2}$')
       then upper(trim(c."COUNTRY_OF_BIRTH")) end   as country_of_birth,
  -- CERTIFIED_FATCA_COMPLIANCE, onto the book's vocabulary. 7, 10 and 11 (a
  -- participating institution, and the two pre-existing-account grades) have no
  -- member. us_person follows the certification: W-9 is a US person, W-8 is not.
  -- CLIENT_SUBJECT_TO_FATCA does not: it is false on the W-9 row as well, and
  -- pairing that with w9 breaks the book's coherence check.
  case c."CERTIFIED_FATCA_COMPLIANCE"
    when '1' then 'w9'
    when '2' then 'w8'
    when '3' then 'w8_cure'
    when '4' then 'other_individual'
    when '5' then 'other_individual'
    when '6' then 'not_certified'
    when '8' then 'other_entity'
    when '9' then 'other_entity'
  end                                               as fatca_status,
  case c."CERTIFIED_FATCA_COMPLIANCE"
    when '1' then true
    when '2' then false
  end                                               as us_person,
  nullif(trim(c."FIRST_PASSPORT_NUMBER"), '')      as passport_number,
  case when regexp_matches(upper(trim(c."COUNTRY_OF_CITIZENSHIP_FIRST_PASSPORT")), '^[A-Z]{2}$')
       then upper(trim(c."COUNTRY_OF_CITIZENSHIP_FIRST_PASSPORT")) end
                                                    as passport_country,
  -- The id sent as externalPartyId. When this firm opened the client, that is our
  -- contact uid. On a client SEI already held it is usually the same number as
  -- CLIENT_ID, which is the link. It is not a second client. Kept here so a call
  -- that has to name externalPartyId can read it; the link stays CLIENT_ID.
  nullif(trim(c."EXTERNAL_PARTY_ID"), '')           as external_party_id,
  -- COMPANY_NAME is the organisation's own name. A person does not have one, and the
  -- book then joins first name and surname. Middle name is not on this report.
  case
    when lower(trim(c."CLIENT_TYPE")) in ('5', 'individual') then null
    else nullif(trim(c."COMPANY_NAME"), '')
  end                                               as entity_name,
  null::varchar                                     as middle_name,
  -- EMAIL, PHONE AND THE FULL ADDRESS come from platform.contact_points, not from this report:
  -- SEI publishes contact details as their own feed. Which one is the client's is not a judgement
  -- this model has to make — SEI states it, with is_primary_email / is_primary_phone /
  -- is_primary_address — and a non-primary is taken only when there is no primary at all, because
  -- an email is more use than a NULL and the fallback is visible here rather than inferred later.
  -- ContactDetails is the feed that says which address is primary. The Clients report
  -- also carries an email and a phone, and a person whose contact row has neither still
  -- has them here. The contact row wins when both exist.
  coalesce(cp.email, nullif(trim(c."EMAIL_ADDRESS"), '')) as email,
  coalesce(ph.mobile, cp.primary_phone, nullif(trim(c."PHONE_NUMBER"), '')) as mobile,
  case
    when ph.other_number is distinct from
         coalesce(ph.mobile, cp.primary_phone, nullif(trim(c."PHONE_NUMBER"), ''))
    then ph.other_number
  end as phone,
  -- One address struct with the same field names every provider uses. Taken from contact_points
  -- rather than from the Clients report's own CORRESPONDENCE_ADDRESS_* columns because those carry
  -- no POSTCODE and no region — a UK address without a postcode is not one you can post to.
  {
      'lines': coalesce(cp.address_lines, []),
      'city': cp.address_city,
      'region': cp.address_region,
      'postcode': cp.address_postcode,
      'country': cp.address_country
  }                                                 as address,
  -- The client's base currency. The Clients report states it (`CLIENT_BASE_CURRENCY`);
  -- a blank row publishes NULL and the book merge treats that as GBP. Accounts carry
  -- their own `ACCOUNT_BASE_CURRENCY`, which is a different fact.
  nullif(trim(c."CLIENT_BASE_CURRENCY"), '')        as base_currency,
  -- SEI's Clients report has no person-level status (no Suspended). Published as a typed
  -- NULL so the book's merge can read `s.status` for every provider; a missing column is a
  -- Binder Error that files the tenant as "unavailable".
  NULL::varchar                                     as status,
  NULL::varchar                                     as status_raw,
  c."COMMENCEMENT_DATE"                             as commenced_on,
  c."ADDRESS_ID"                                    as address_id,
  list_filter([c."CORRESPONDENCE_ADDRESS_LINE1", c."CORRESPONDENCE_ADDRESS_LINE2",
               c."CORRESPONDENCE_ADDRESS_LINE3"], x -> x is not null and x <> '')
                                                    as address_lines,
  c."CORRESPONDENCE_ADDRESS_CITY"                   as address_city,
  c."CORRESPONDENCE_ADDRESS_COUNTRY"                as address_country,
  p.address_purposes,
  -- THE ISA ALLOWANCE, which SEI states per CLIENT and not per account — and that is the whole
  -- shape of it. An allowance is a person's, per tax year, so a client holding two ISAs shares one
  -- and any reader that SUMS across their accounts reports double what they have used.
  -- `platform.accounts` carries it onto each account of the wrapper (which is what the portal's
  -- wrapper card groups by) and `GET /crm/home` takes max() per (contact, product).
  --
  -- Used is `ISA_CONTRIBUTION_TO_DATE`: what has been subscribed this year. It is the same
  -- figure as `CASH_ISA_SUBSCRIPTION_AMOUNT` on this drop, and it is what this year's deposit
  -- rows add up to. `STOCK_AND_SHARE_ISA_SUBSCRIPTION_AMOUNT` is zero on every client but one,
  -- so reading it as the subscription showed a full allowance where a contribution had been
  -- made. Headroom is what may still go in.
  c."ISA_CONTRIBUTION_TO_DATE"                      as isa_subscription_used,
  c."TOTAL_REMAINING_ISA_HEADROOM_AMOUNT"           as isa_headroom_remaining,
  coalesce(g.firm_id, c."FIRM_ID")                  as firm_id,
  -- The client's OWN subfirm, which the report does not fill. Kept so a reader can see that
  -- the source said nothing. The slice predicate filters `subfirm_ids`, not this.
  coalesce(g.subfirm_id, c."RESIDING_SUBFIRM_ID")   as subfirm_id,
  coalesce(sf.subfirm_ids, []::VARCHAR[])           as subfirm_ids,
  coalesce(g.dsr_team_ids, [])                      as dsr_team_ids,
  coalesce(g.adviser_firm_dsr_ids, [])              as adviser_firm_dsr_ids,
  c.dt                                              as as_of_date
from current c
left join purposes p on p.client_id = c."CLIENT_ID"
left join grants g on g.client_id = c."CLIENT_ID"
left join contacts cp on cp.entity_external_id = c."CLIENT_ID"
left join phones ph on ph.entity_external_id = c."CLIENT_ID"
left join account_subfirms sf on sf.client_id = c."CLIENT_ID"
left join titles t on t.code = trim(c."TITLE")
left join title_aliases ad on ad.source = t.description
left join title_aliases aw on aw.source = trim(c."TITLE")
