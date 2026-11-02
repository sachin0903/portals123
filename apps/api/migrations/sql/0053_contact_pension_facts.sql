-- crm.contacts: the pension and identity facts our own integrations already ask for.
--
-- 0052 added `gender` because Morgan Lloyd refuses a contact without it. This adds the rest of
-- the set, and the evidence for each is that something in this repo ALREADY wants it:
--
--   * `app/flows/onboarding_seed.py` collects marital status, employment status and retirement
--     age as REQUIRED questions -- and there was nowhere for the answers to land.
--   * `cam_createcontact` takes 35 parameters (verified against the org's CSDL); we send 13.
--     `familystatuscode`, `cam_retirementageint`, `cam_employmentstatus`, `mobilephone`,
--     `cam_sourceofwealth` and `cam_previouslytakenflexiaccessbenefits` were unsendable.
--   * `app/integrations/ctc/xml_builder.py` validates `investor.marital_status` and
--     `investor.sex`, and the illustration endpoint left both unset.
--
-- Expand phase: nullable columns on a table that already has its RLS policies and its
-- audit.attach from 0003. Nothing is rewritten and nothing becomes required.
--
-- ON VOCABULARIES. Each of these words means something slightly different to each
-- counterparty, so the canonical value is stored here and mapped at the boundary -- the same
-- shape as `status` / `status_raw` elsewhere. Concretely:
--
--   marital_status  -- superset of Dynamics `familystatuscode`
--                      {1 Single, 2 Married, 5 Civil Partner, 6 Divorced, 7 Cohabiting,
--                      8 Widowed} and of onboarding's four. CTC has its own shorter list.
--   employment_status -- onboarding's vocabulary.
--
--                      THIS COMMENT WAS WRONG AND IS CORRECTED IN PLACE (Sept 2026). It said
--                      the field was "deliberately NOT mapped to Dynamics'
--                      `cam_employmentstatus`, which despite the name is an OCCUPATIONAL
--                      taxonomy (Business Owner, C-Suite, Doctors, Influencers...)". The
--                      vendored manifest says otherwise: that field documents exactly
--                      {Employed, Self Employed, Unemployed, Student, Child, Carer, Child
--                      Carer, Pensioner, Other}, and the words "Business Owner" and "C-Suite"
--                      appear nowhere in `vendor_manifest.json` or in `morgan_lloyd.enums`.
--                      All eight values below have a counterpart there (theirs is the
--                      superset — `Child` has none of ours), so the refusal was costing us a
--                      field the vendor asks for and we already hold. Mapped in
--                      `integrations/morgan_lloyd/contacts.py::_EMPLOYMENT_STATUS`.
ALTER TABLE crm.contacts
  ADD COLUMN IF NOT EXISTS marital_status text
    CONSTRAINT contacts_marital_status_check
    CHECK (marital_status IS NULL OR marital_status IN
      ('single', 'married', 'civil_partner', 'divorced', 'cohabiting', 'widowed')),

  -- Intended retirement age. A property of the person, not of one illustration: it is the
  -- default for `IllustrationInput.exit_age` and what `cam_retirementageint` carries. Bounds
  -- match the onboarding question (50-75) -- below 55 is only reachable under protected
  -- pension age or ill health, so the floor is deliberately permissive rather than 55.
  ADD COLUMN IF NOT EXISTS retirement_age smallint
    CONSTRAINT contacts_retirement_age_check
    CHECK (retirement_age IS NULL OR retirement_age BETWEEN 50 AND 75),

  ADD COLUMN IF NOT EXISTS employment_status text
    CONSTRAINT contacts_employment_status_check
    CHECK (employment_status IS NULL OR employment_status IN
      ('employed', 'self_employed', 'retired', 'unemployed', 'education',
       'care_under_16s', 'care_over_16', 'other')),

  -- Dynamics has both `telephone1` and `mobilephone`; we had one `phone`, so whichever number
  -- a client gave was sent as the landline.
  ADD COLUMN IF NOT EXISTS mobile_phone text,

  -- AML-relevant and required by Dynamics' own contact form. Free text against their 20-member
  -- option set rather than a CHECK: the list is long, theirs, and likely to grow, and a CHECK
  -- that lags it would reject a legitimate answer.
  ADD COLUMN IF NOT EXISTS source_of_wealth text,

  -- THE MPAA TRIGGER. Taking flexible benefits cuts the annual allowance from £60,000 to
  -- £10,000, so this is not merely a Dynamics field (`cam_previouslytakenflexiaccessbenefits`)
  -- -- it changes what a contribution is allowed to be. `platform.contributions` computes
  -- relief without it today, which means an over-allowance contribution cannot be caught.
  ADD COLUMN IF NOT EXISTS flexi_access_taken boolean,

  -- `build_contact` hardcodes `cam_potentiallyvulnerable = "False"` for every client, which is
  -- an assertion we have no basis for making. Holding the flag lets it be answered honestly.
  ADD COLUMN IF NOT EXISTS is_vulnerable boolean,
  ADD COLUMN IF NOT EXISTS vulnerability_notes text;

COMMENT ON COLUMN crm.contacts.marital_status IS
  'Canonical marital status; superset of Dynamics familystatuscode and of CTC''s list.';
COMMENT ON COLUMN crm.contacts.retirement_age IS
  'Intended retirement age (50-75). Default for illustration exit_age; ML cam_retirementageint.';
COMMENT ON COLUMN crm.contacts.employment_status IS
  'Employment status per onboarding. NOT Dynamics cam_employmentstatus, which is occupational.';
-- (The wording above is stale — see the corrected `--` note at the top of this file. Left
-- verbatim on purpose: this migration is applied, and re-wording an applied COMMENT ON makes a
-- fresh database disagree with an existing one. A new migration is the way to change it, and it
-- has not been worth one.)
COMMENT ON COLUMN crm.contacts.flexi_access_taken IS
  'Has taken flexible benefits: triggers the £10,000 MPAA in place of the annual allowance.';
COMMENT ON COLUMN crm.contacts.is_vulnerable IS
  'Vulnerable-client flag. ML cam_potentiallyvulnerable was hardcoded false before this.';
