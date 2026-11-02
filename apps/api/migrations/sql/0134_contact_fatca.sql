-- ============================================================================
-- 0134_contact_fatca.sql -- the FATCA self-certification, and the passport SEI hands back.
--
-- 0053's and 0116's pattern a third time: a fact a custodian REQUIRES, that a surface shows, and
-- that had nowhere to land but the generic `crm.contacts.data` overflow.
--
-- ---------------------------------------------------------------------------
-- FATCA is required by SEI enrolment and by nobody else.
-- ---------------------------------------------------------------------------
--
-- `certifiedFATCAComplianceId` is a REQUIRED field on `POST /accountservice/accounts`, and the
-- string "fatca" appears **zero times** in Morgan Lloyd's vendor manifest and zero times in
-- `pkgs/seccl`'s schemas. So this is not a platform-wide gap; it is the one custodian that asks.
--
-- SEI's `FATCA_COMPLIANCE` code set has eleven members, and for UK retail the answer is one of
-- two: **1 = W-9** (a US person) or **2 = W-8** (everyone else), with `PURPOSE_OF_W8` = 1 (Account
-- Open). The other nine are cures, entity self-certifications and pre-existing-account grades.
-- So a single question answers it: "are you a US citizen or US tax resident?"
--
-- ---------------------------------------------------------------------------
-- IT IS ASKED, NEVER DERIVED. This is the `tax_relief` rule, and it applies exactly.
-- ---------------------------------------------------------------------------
--
-- `crm.contacts.tax_residency` already holds ISO codes, so `'US' = ANY(tax_residency)` looks like
-- a free answer. It is not one. A W-8BEN or W-9 is a SIGNED SELF-CERTIFICATION, and 0116 settled
-- the estate's position on those for `tax_relief`: "the four options are first-person DECLARATIONS
-- the member makes, and asserting one on their behalf would put a false declaration on a pension
-- record." A FATCA certification is the same kind of statement to a tax authority, so it is stored
-- as the answer the client gave, with the date they gave it, and `flows/onboarding_seed.py` asks
-- for it beside nationality, tax residency and PEP — which it currently does not.
--
-- The CANONICAL value is stored and mapped at the boundary, the way `marital_status` is: SEI's
-- code is firm-agnostic here (blank FIRM_ID) but the vocabulary is SEI's, and a second custodian
-- asking for FATCA would want its own spelling.
--
-- ---------------------------------------------------------------------------
-- The passport, which SEI publishes back to us and we cannot store.
-- ---------------------------------------------------------------------------
--
-- `updateClientBasicProfile` takes `firstCountryPassport.{countryOfCitizenship1,
-- firstPassportNumber}`, and SEI's own `Clients` report publishes `FIRST_PASSPORT_NUMBER` and
-- `COUNTRY_OF_CITIZENSHIP_FIRST_PASSPORT` straight back — so the round trip exists and only our
-- column was missing.
--
-- ---------------------------------------------------------------------------
-- What is deliberately NOT here: accountClassificationId.
-- ---------------------------------------------------------------------------
--
-- Enrolment requires it and it needs no column, for two reasons. Its nine members (Individual,
-- Corporate, Charity, Estate, Trust, Collective, Government, Pension, House Account) are already
-- derivable from `crm.contacts.type` + `contact_sub_type` + `platform.products.is_pension`. And
-- the id is **FIRM-SCOPED**: 48527 is firm 3106's "Individual" and 48518 is firm 3102's, nine
-- members per firm across six firms. So it must be resolved per firm from `ref.codes` at call
-- time — a resolver in `app/integrations/sei/reference.py`, not a stored constant, and certainly
-- not a column holding one firm's number.
-- ============================================================================

ALTER TABLE crm.contacts
  -- The question actually asked. NULL is "not asked yet", which is different from "no".
  ADD COLUMN IF NOT EXISTS us_person          boolean,
  -- The certification given, canonical. Mapped to SEI's eleven-member code at the boundary.
  ADD COLUMN IF NOT EXISTS fatca_status       text,
  -- A certification has a date; without one nobody can tell a current declaration from a stale one.
  ADD COLUMN IF NOT EXISTS fatca_certified_at date,
  ADD COLUMN IF NOT EXISTS passport_number    text,
  -- ISO 3166-1 alpha-2, joining `ref.countries` like `nationality` and `tax_residency` (0050) —
  -- NOT a display name. `country_of_birth` holds names and that inconsistency is called out in
  -- CLAUDE.md; a new column does not add to it.
  ADD COLUMN IF NOT EXISTS passport_country   text;

-- The canonical vocabulary, not SEI's numbers. `w9` and `w8` are the two a UK retail client gives;
-- the rest exist because SEI's set has them and a client who arrives mid-cure has an answer.
ALTER TABLE crm.contacts
  ADD CONSTRAINT contacts_fatca_status_check
  CHECK (fatca_status IS NULL OR fatca_status IN (
    'w9',                 -- SEI 1: a US person
    'w8',                 -- SEI 2: not a US person (the common answer)
    'w8_cure',            -- SEI 3
    'other_individual',   -- SEI 4/5: other individual self-certification
    'other_entity',       -- SEI 8/9: other entity self-certification
    'not_certified'       -- SEI 6: the firm has not certified
  ));

-- The two halves of one fact must agree. A `us_person` of true with a `w8` certification is a
-- client declaring they are not a US person on a record saying they are — exactly the incoherence
-- 0054's vulnerability CHECK exists to prevent, and the kind a boundary cannot catch because both
-- values are individually valid.
ALTER TABLE crm.contacts
  ADD CONSTRAINT contacts_fatca_coherent
  CHECK (
    us_person IS NULL
    OR fatca_status IS NULL
    OR (us_person IS TRUE  AND fatca_status <> 'w8')
    OR (us_person IS FALSE AND fatca_status <> 'w9')
  );

COMMENT ON COLUMN crm.contacts.us_person IS
  'Whether the client declared themselves a US citizen or US tax resident. ASKED, never derived '
  'from tax_residency: a W-8BEN/W-9 is a signed self-certification, and asserting one on a '
  'client''s behalf puts a false declaration on the record (the tax_relief rule, 0116).';
COMMENT ON COLUMN crm.contacts.fatca_status IS
  'The certification held, canonical. Maps to SEI''s 11-member FATCA_COMPLIANCE set at the '
  'boundary — required by POST /accountservice/accounts. Neither Morgan Lloyd nor Seccl asks.';
COMMENT ON COLUMN crm.contacts.passport_country IS
  'ISO 3166-1 alpha-2, joining ref.countries. SEI publishes it back as '
  'COUNTRY_OF_CITIZENSHIP_FIRST_PASSPORT, so the round trip exists.';
