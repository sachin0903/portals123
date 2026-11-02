-- ============================================================================
-- 0138_contact_employment.sql -- the client's employer, as a fact about the PERSON.
--
-- 0053's, 0116's and 0121's pattern a fourth time, with one difference: this fact did have a
-- column, and the column was on the wrong table.
--
-- ---------------------------------------------------------------------------
-- WHERE IT WAS, AND WHY THAT WAS WRONG.
-- ---------------------------------------------------------------------------
--
-- `platform.contributions` (0051) carries a nine-column contributor block -- `employer_name`,
-- three address lines, city, county, postcode, phone, email -- with this rationale in its own
-- header: "Required by cam_createcontribution whenever the contribution type is NOT personal (the
-- docs' `*` rule), and held nowhere else -- the payer of an employer contribution is not the
-- client, so crm.contacts cannot supply it."
--
-- The first half is true and the second half does not follow. The payer of an employer
-- contribution is not the client, but it IS the client's employer, and who somebody works for is
-- a fact about them that changes when they change jobs -- not a fact about one £500 payment. The
-- consequence was that the classify panel asked an adviser to type an employer's name, address,
-- town, postcode and email ONCE PER CREDIT, for a monthly contribution, from an adviser who
-- usually could not have known the address; and the same employer was then held under n spellings
-- across n contributions with nothing able to tell they were one company.
--
-- So the NAME moves to the person. The contribution keeps its own copy, and that is deliberate:
-- it is what was actually SENT to Morgan Lloyd for that payment, and a client changing employer in
-- March must not restate what was filed in February. `crm.contacts.employer_name` is the DEFAULT
-- the classify path reads; `platform.contributions.employer_name` stays the record of fact.
--
-- ---------------------------------------------------------------------------
-- THE ADDRESS AND EMAIL DO NOT MOVE. THEY GO.
-- ---------------------------------------------------------------------------
--
-- `cam_createcontribution` marks the contributor's address block and email OPTIONAL (only the name
-- carries the vendor's `*`), and `integrations/morgan_lloyd/contributions.py` already sends only
-- what is present. Four fields nobody requires, asked per payment, of somebody who does not know
-- them, is a form that trains an adviser to skip a form. The COLUMNS stay on the contribution --
-- rows hold real values and the vendor still accepts them -- but nothing collects them any more.
--
-- ---------------------------------------------------------------------------
-- `relevant_uk_earnings` IS NOT RENAMED. IT IS RELABELLED.
-- ---------------------------------------------------------------------------
--
-- The client record now shows this figure as "Annual income", which is the phrase an adviser and a
-- client both use and the phrase Morgan Lloyd uses (`cam_annualearnings`). The COLUMN keeps the
-- HMRC term, because that is what it means: relievable contributions are tested against relevant
-- UK earnings, which is a defined statutory quantity and NOT the same thing as everything on a
-- P60. Renaming it would put a friendlier word on a column whose whole job is to be the statutory
-- one -- and `contributions/relief.ts`, `contributions.py` and `cam_annualearnings` all read it by
-- that name. A label is the right place for the friendlier word; see `docs/contributions.md`.
-- ============================================================================

ALTER TABLE crm.contacts
  ADD COLUMN IF NOT EXISTS employer_name text;

COMMENT ON COLUMN crm.contacts.employer_name IS
  'Who this client works for, in the firm''s own words. The DEFAULT for an employer '
  'contribution''s contributor name (cam_employerothername); platform.contributions.employer_name '
  'stays the record of what was actually filed for a given payment. Free text, deliberately: '
  'Morgan Lloyd''s cam_employername is a GUID into their own Account table and we hold no such '
  'mapping, so a code here would be a code for nothing.';

COMMENT ON COLUMN crm.contacts.relevant_uk_earnings IS
  'The HMRC figure a relievable contribution is tested against (FA 2004 s.189) -- NOT gross pay. '
  'Shown on the client record as "Annual income" and sent as cam_annualearnings; the column keeps '
  'the statutory name because that is what the relief maths in app/contributions.py and '
  'contributions/relief.ts computes with.';

-- No RLS change: `crm.contacts` already carries its policies (0002) and this is one more column on
-- a table a firm's staff and the client's own portal principal can already read.
