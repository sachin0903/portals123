-- ============================================================================
-- 0129_contact_kyc_facts.sql -- the identity answers onboarding already collects, given columns.
--
-- THIS IS 0053'S PATTERN A SECOND TIME, and the pattern is worth naming because it keeps
-- happening: `flows/onboarding_seed.py` asks a question, the answer has no column, so the
-- mapping folds it into `crm.contacts.data` and the seed's own comment says as much --
--
--     "`source_of_wealth` is a real column; the rest have none and fold into
--      `crm.contacts.data` ... Mapping them anyway is the point: an answer in `data` is at
--      least ON the client, where a form response is not."
--
-- `data` is the right home for a firm's OWN added questions. It is the wrong home for a fact the
-- platform itself asks for, sends to a custodian, or shows on a profile, because a jsonb key is
-- not queryable, not typed, not editable through the client form, and reaches the record only via
-- the generic `data` flattener that renders it as a dotted path.
--
-- Five of those answers are platform facts and get columns here. Two more are new: `tax_relief`
-- and `vulnerability_characteristics`, both of which Morgan Lloyd asks for on every contact and
-- neither of which we could answer.
--
-- WHAT IS DELIBERATELY NOT HERE, so the omissions are decisions rather than oversights:
--
--   * `pep` -- onboarding asks it and it stays in `data` for now. A SELF-DECLARED PEP flag and a
--     screening OUTCOME are different facts with different rules, and `crm.contacts` is readable
--     by the client themselves through `auth.client_scope()` while `aml.*` deliberately is not
--     (docs/aml.md, the tipping-off control). A column here would sit one careless join away from
--     being read as an adjudication. It needs its own naming and its own decision.
--   * the `account_activity_*` answers (funds in/out, turnover, transaction scale, nature of
--     activities, standing orders). Those describe an ACCOUNT, not a person, and belong on
--     `platform.accounts` -- a separate migration and a separate surface.
--
-- Expand phase throughout: nullable columns on a table that already carries its RLS policies and
-- its `audit.attach` from 0002. Landing them there is the point -- this is KYC-relevant personal
-- data and must never become a side-table the audit spine does not see.
-- ============================================================================

ALTER TABLE crm.contacts
  -- ── occupation, WHICH IS NOT EMPLOYMENT STATUS ──────────────────────────────────────────
  --
  -- Two different questions, and Morgan Lloyd is the one that models it correctly: their
  -- `cam_employmentstatus` is {Employed, Self Employed, Unemployed, Student, Child, Carer, Child
  -- Carer, Pensioner, Other} -- whether the person works -- and they have NO occupation field at
  -- all. Ours is the same distinction: `employment_status` (0053) answers whether they work,
  -- this answers what they do. A retired GP is `retired` AND `GP / Doctor`, and collapsing the
  -- two loses whichever half you drop.
  --
  -- FREE TEXT, no CHECK, for the reason `source_of_wealth` gives about itself: the onboarding
  -- question offers ~97 occupations plus "Other", the list is presentational and firms will add
  -- to it, and a CHECK that lagged it would reject a legitimate answer. `occupation_detail`
  -- carries the "Other" free text and the retired client's previous occupation -- the seed asks
  -- for both under conditions that are mutually exclusive, so one column holds either.
  ADD COLUMN IF NOT EXISTS occupation text,
  ADD COLUMN IF NOT EXISTS occupation_detail text,

  -- ── place of birth ─────────────────────────────────────────────────────────────────────
  --
  -- Standard CDD identity evidence.
  --
  -- UNCONSTRAINED, AND THAT IS A CORRECTION rather than laziness. This first shipped with
  -- `CHECK (country_of_birth ~ '^[A-Z]{2}$')` on the assumption that the onboarding combobox
  -- posts an ISO 3166-1 alpha-2 code. IT DOES NOT: `onboarding_seed.COUNTRIES` is a list of
  -- display NAMES and `_opts` makes `{value: name, label: name}`, so the answer is
  -- "United Kingdom". The CHECK therefore rejected the value the platform's own onboarding
  -- produces and failed the whole fact-find step — caught by
  -- `tests/test_flows_onboarding_walkthrough.py`, which drives the real seed, and by nothing
  -- else, because every unit test around it supplied a code.
  --
  -- So the column stores what the question posts. `nationality` (0002) is an unconstrained
  -- `text[]` holding codes, which means the estate is inconsistent about how a country is
  -- spelled — that is real and is worth one deliberate change (give the question codes, migrate
  -- both), not a constraint here that breaks onboarding to look tidy.
  ADD COLUMN IF NOT EXISTS country_of_birth text,
  ADD COLUMN IF NOT EXISTS town_of_birth text,

  -- ── source of FUNDS, which is not source of WEALTH ─────────────────────────────────────
  --
  -- The JMLSG distinction and onboarding asks both: wealth is how the money was accumulated over
  -- a lifetime ("Sale of UK private company in 1997"), funds is where THIS payment came from
  -- ("Transfer from another custodian"). A single column would answer one question and be read as
  -- the other. `source_of_wealth` (0053) is the narrative; this is the closed-ish list, free text
  -- for the same reason as occupation, with `_detail` for its "Other (please specify)".
  ADD COLUMN IF NOT EXISTS source_of_funds text,
  ADD COLUMN IF NOT EXISTS source_of_funds_detail text,

  -- ── tax relief: the member's own DECLARATION ───────────────────────────────────────────
  --
  -- Morgan Lloyd's `cam_taxrelief`, whose four values are first-person statements a member makes
  -- about their own tax position ("I have relevant UK earnings chargeable to income tax...").
  -- `morgan_lloyd.enums.TaxRelief` + TAX_RELIEF_DESCRIPTIONS hold the full wording.
  --
  -- WHY THIS IS A COLUMN AND NOT DERIVED. `relevant_uk_earnings` (0051) already holds a figure,
  -- and it is tempting to send "I have relevant UK earnings" whenever that figure is present.
  -- That would be asserting a declaration on the client's behalf -- putting words in their mouth
  -- on a pension record -- so `integrations/morgan_lloyd/contacts.py` deliberately refused to
  -- infer it, and this is what makes it answerable instead.
  --
  -- CHECKED, unlike occupation, because this one IS closed: the vendor accepts exactly four
  -- values, an unlisted answer has nowhere to go, and the slugs are their enum member names.
  ADD COLUMN IF NOT EXISTS tax_relief text
    CONSTRAINT contacts_tax_relief_check
    CHECK (tax_relief IS NULL OR tax_relief IN
      ('uk_relevant_earnings',
       'uk_resident_this_year',
       'overseas_crown_employment_no_uk_earnings',
       'spouse_overseas_crown_employment')),

  -- ── vulnerability CHARACTERISTICS: the FCA's sub-taxonomy under the four drivers ────────
  --
  -- 0054 added `vulnerability_reasons`, the four FG21/1 DRIVERS (health / life_event /
  -- resilience / capability). This is the layer beneath: the 24 characteristics FG21/1 lists
  -- under those drivers, which is also exactly what Morgan Lloyd's
  -- `cam_vulnerabilitycharacteristics` accepts -- so the vocabulary is the regulator's on both
  -- sides and this is a mapping rather than a translation, the same property that made the
  -- drivers a 1:1 map.
  --
  -- IT ALSO CLOSES A GAP THAT WAS BLOCKING A REAL CALL. The vendor marks the vulnerability
  -- detail `No**` -- required once `cam_potentiallyvulnerable` is "True" -- and we held nothing
  -- for this one, so a vulnerable client was pushed with the flag and part of the detail it
  -- makes required.
  --
  -- AN ARRAY, for 0054's reason restated one level down: the characteristics compound even more
  -- than the drivers do. A bereavement (life event) that leaves someone unable to absorb a loss
  -- (low emotional resilience) with a hospital stay behind it (long-term illness) is one client
  -- with three, and being made to pick the "main" one is a judgement the record should not demand.
  --
  -- NOT constrained to be consistent with `vulnerability_reasons`. The grouping is real and the
  -- UI shows it, but enforcing "a characteristic only from a selected driver" would make the
  -- grouping load-bearing -- and if our reading of FG21/1's table were even slightly off, a
  -- legitimate characteristic would become unrecordable and only a migration could fix it. The
  -- coherence that IS enforced is the one 0054 enforces: no detail without the flag.
  ADD COLUMN IF NOT EXISTS vulnerability_characteristics text[]
    CONSTRAINT contacts_vulnerability_characteristics_check
    CHECK (
      vulnerability_characteristics IS NULL
      OR vulnerability_characteristics <@ ARRAY[
        -- health
        'physical_disability', 'long_term_illness', 'hearing_impairment', 'visual_impairment',
        'mental_health', 'addiction', 'low_mental_capacity',
        -- life events
        'retirement', 'bereavement', 'income_shock', 'relationship_breakdown', 'domestic_abuse',
        'caring_responsibilities', 'other_circumstances',
        -- resilience
        'erratic_income', 'over_indebtedness', 'low_savings', 'low_emotional_resilience',
        -- capability
        'low_financial_knowledge', 'poor_literacy_numeracy', 'poor_english', 'poor_digital_skills',
        'learning_difficulties', 'no_access_to_support'
      ]::text[]
    );

-- The flag and its detail stay coherent, extending 0054's own constraint to the new column.
-- Written as a SEPARATE constraint rather than by replacing 0054's: dropping and recreating that
-- one would take a fuller lock and lose its name from anything referencing it, and two named
-- constraints each say what they check.
ALTER TABLE crm.contacts
  ADD CONSTRAINT contacts_vulnerability_characteristics_coherent
  CHECK (coalesce(is_vulnerable, false) OR vulnerability_characteristics IS NULL);

COMMENT ON COLUMN crm.contacts.occupation IS
  'What the client does. NOT employment_status, which is whether they work — a retired GP is '
  'both. Free text: the onboarding list is presentational and grows.';
COMMENT ON COLUMN crm.contacts.occupation_detail IS
  'The "Other" occupation, or a retired client''s previous occupation and aggregated wealth. '
  'One column because the two onboarding questions are mutually exclusive.';
COMMENT ON COLUMN crm.contacts.country_of_birth IS
  'Country of birth, as the onboarding question posts it (a display name today, not an ISO '
  'code — nationality holds codes, and reconciling the two is its own change).';
COMMENT ON COLUMN crm.contacts.town_of_birth IS 'Town of birth (CDD identity evidence).';
COMMENT ON COLUMN crm.contacts.source_of_funds IS
  'Where THIS payment came from (JMLSG). Distinct from source_of_wealth, which is how the '
  'money was accumulated over a lifetime.';
COMMENT ON COLUMN crm.contacts.source_of_funds_detail IS
  'The "Other (please specify)" source of funds.';
COMMENT ON COLUMN crm.contacts.tax_relief IS
  'The member''s own tax-relief DECLARATION (Morgan Lloyd cam_taxrelief; '
  'morgan_lloyd.enums.TaxRelief). Never derived from relevant_uk_earnings — holding a figure is '
  'not the client having declared anything.';
COMMENT ON COLUMN crm.contacts.vulnerability_characteristics IS
  'FCA FG21/1 characteristics beneath the four drivers in vulnerability_reasons (0054), and '
  'exactly what ML cam_vulnerabilitycharacteristics accepts. An array because they compound.';
