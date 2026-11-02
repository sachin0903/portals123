-- ============================================================================
-- 0139_client_sections.sql -- the facts the restructured client record asks for, and the
-- beneficiary nominations a pension needs.
--
-- The client record was ONE "Details" card holding twenty-odd fields in a flat register, edited
-- through one form and one save. Sept 2026 breaks it into five independently-editable sections --
-- contact, personal, pension, employer, compliance -- each shown in full whether or not it is
-- filled in. That is a presentation change and mostly needed no schema; these are the five facts
-- the sections ask for that nothing held, plus the one table.
--
-- ---------------------------------------------------------------------------
-- WHY "SHOW EVERY FIELD" NEEDED A SCHEMA CHANGE AT ALL.
-- ---------------------------------------------------------------------------
--
-- The old read view DROPPED any fact with no value, which is right for a register of whatever
-- happens to be recorded and wrong for a section a firm is meant to complete: a field nobody can
-- see is a field nobody fills in, which is exactly how `relevant_uk_earnings` (0051) went five
-- migrations without an editor while the HMRC relievable-limit check ran on the £3,600 floor. A
-- section that always states its own fields makes the gaps visible -- and then the gaps have to be
-- real columns rather than dotted paths under `crm.contacts.data`, which is 0116's argument again.
--
-- ---------------------------------------------------------------------------
-- THE EMPLOYER BLOCK IS GATED ON A FACT, NOT ON A GUESS.
-- ---------------------------------------------------------------------------
--
-- `employer_contributions` is the gate for the whole Employer section (and it only appears at all
-- for a client holding a pension). It is ASKED rather than derived from "does this client have an
-- employer contribution on the ledger": a member whose employer is about to start contributing has
-- no such row yet and still needs the company's details captured before the first payment, and a
-- member whose employer stopped last year still has the rows. Deriving it would mean the section
-- appears only once it is too late to have filled it in.
--
-- `employer_registration_number` is Companies House's, and it is TEXT: a company number is
-- `SC` or `NI` prefixed outside England and Wales, and leading zeros are significant
-- (`00445790` is Marks and Spencer). An integer column would silently become `445790`.
--
-- `employer_address` is jsonb and NOT the reuse of `crm.contacts.address`: that column is where
-- the CLIENT lives, and the payer of an employer contribution is a company. Same shape as the
-- client's, so one `AddressLookup` serves both.
--
-- ---------------------------------------------------------------------------
-- `flexi_access_date` -- because "when" is a different fact from "whether".
-- ---------------------------------------------------------------------------
--
-- `flexi_access_taken` (0053) is the MPAA trigger and cuts the annual allowance from £60,000 to
-- £10,000. The date it happened is what decides WHICH tax year the reduced allowance first applies
-- to, and a contribution booked for the year before is unaffected -- so a flag alone cannot answer
-- the question the flag exists to raise. Nullable on purpose: the flag can be true from a client's
-- own declaration with the date not to hand, and refusing to record the trigger for want of a date
-- would be the worse failure.
--
-- ---------------------------------------------------------------------------
-- `source_of_wealth_detail` -- the "Other" answer.
-- ---------------------------------------------------------------------------
--
-- The record now offers Morgan Lloyd's own twelve-member list rather than a free-text box, because
-- `integrations/morgan_lloyd/contacts.py::_SOURCE_OF_WEALTH` sends the vendor's INT only on an
-- exact label match -- so a firm typing "salary " or "Investments" filled our column and left the
-- vendor's structured field empty. Picking from the list fixes that and creates the usual problem:
-- one of the twelve IS "Other", which means nothing without the words. This is those words, and it
-- is what `cam_sourceofwealthstring` carries when Other is chosen (the label itself otherwise).
-- The `source_of_funds`/`source_of_funds_detail` pair from 0116 in the same shape.
-- ============================================================================

ALTER TABLE crm.contacts
  ADD COLUMN IF NOT EXISTS employer_contributions        boolean,
  ADD COLUMN IF NOT EXISTS employer_registration_number  text,
  ADD COLUMN IF NOT EXISTS employer_address              jsonb,
  ADD COLUMN IF NOT EXISTS flexi_access_date             date,
  ADD COLUMN IF NOT EXISTS source_of_wealth_detail       text;

COMMENT ON COLUMN crm.contacts.employer_contributions IS
  'Does this member receive employer contributions? ASKED, never derived from the ledger: a '
  'member whose employer is about to start has no rows yet and still needs the company captured '
  'before the first payment. Gates the Employer section on the client record.';
COMMENT ON COLUMN crm.contacts.employer_registration_number IS
  'Companies House number. TEXT because it is SC/NI-prefixed outside England and Wales and its '
  'leading zeros are significant (00445790).';
COMMENT ON COLUMN crm.contacts.flexi_access_date IS
  'When benefits were flexibly accessed. Decides which tax year the £10,000 MPAA first applies '
  'to, which flexi_access_taken alone cannot answer. Nullable: the flag can be declared without '
  'the date to hand, and refusing to record the trigger for want of one would be worse.';
COMMENT ON COLUMN crm.contacts.source_of_wealth_detail IS
  'The words behind an "Other" source of wealth. Sent as cam_sourceofwealthstring when Other is '
  'chosen; the label itself is sent otherwise.';

-- ============================================================================
-- crm.beneficiaries -- who the member nominates, and for how much.
--
-- ---------------------------------------------------------------------------
-- `crm`, NOT `platform`, and the collision is the reason.
-- ---------------------------------------------------------------------------
--
-- Morgan Lloyd publishes a conformed `platform.beneficiaries` into the lake, and CLAUDE.md records
-- that it deliberately does NOT merge into OLTP. Creating `platform.beneficiaries` here would put
-- our firm-authored nominations under the name the provider pipeline already uses for the
-- custodian's, and the day that merge is wanted the two would have to be told apart by whoever is
-- reading. It also IS a CRM fact: a nomination is a client's expressed wish, captured by the firm,
-- amended by the firm, and (unlike a holding or a valuation) never published back to us.
--
-- It hangs off the ACCOUNT rather than the contact because that is the grain the nomination has:
-- `cam_createbeneficiaries` is addressed by `schemeReference`, a member with two pensions can
-- nominate differently on each, and expression-of-wish forms are signed per scheme.
--
-- ---------------------------------------------------------------------------
-- PROPORTIONS ARE NOT CONSTRAINED TO SUM TO 100, DELIBERATELY.
-- ---------------------------------------------------------------------------
--
-- The obvious constraint is a trigger asserting the set totals 100%. It is wrong here for the same
-- reason `platform.reconciliation_breaks` is unbuilt: a nomination set is edited a row at a time,
-- and a rule that must hold across rows makes the FIRST insert fail -- adding a 50% beneficiary to
-- an empty account would be refused for totalling 50. The UI states the running total and warns;
-- the API refuses a set that exceeds 100 on the way out. A per-row CHECK is what a single row can
-- honestly promise, so that is what is written here.
-- ============================================================================

CREATE TABLE IF NOT EXISTS crm.beneficiaries (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  uid         text        NOT NULL UNIQUE DEFAULT nanoid(12),
  tenant_id   int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
  -- BOTH a plain FK and a composite one, which is 0035's convention and what
  -- `test_every_client_link_is_tenant_contained` asserts in each direction: the single-column key
  -- is what makes the column a declared link at all (and carries the ON DELETE behaviour), the
  -- composite is what contains it to its own tenant. The invariant refuses a composite on a column
  -- with no plain link, so writing only the composite fails the other half of the same test.
  --
  -- Why the containment is a FOREIGN KEY and not a policy, and `test_every_client_link_is_tenant_
  -- contained` names this table if they are missing. The read policy's client branch is
  -- `contact_id = ANY (auth.contact_scope())` and carries NO tenant conjunct, so a row whose
  -- contact_id pointed into another firm would be readable by THAT firm's client. Tightening the
  -- policy would not close it: none of these policies binds `service_role`, which is what
  -- `firm_session` and the worker actually write as. Referential integrity does, for every role,
  -- because RI checks bypass RLS.
  account_id  bigint      NOT NULL REFERENCES platform.accounts(id) ON DELETE CASCADE,
  -- Denormalised from the account so the client-scoped read policy can test it without a join:
  -- `auth.contact_scope()` answers in contact ids, and a policy that has to join to find one is a
  -- policy that runs per row on every read.
  contact_id  int         REFERENCES crm.contacts(id) ON DELETE SET NULL,

  -- Morgan Lloyd takes ONE `cam_nameofbeneficiary` and no title, so the title is ours: a firm's
  -- expression-of-wish form asks for it, and dropping it because one vendor has no field would
  -- lose what the client actually wrote. Concatenated at the boundary, never stored joined.
  title       text,
  name        text        NOT NULL CHECK (length(trim(name)) > 0),
  relationship text,
  -- Percentage of the fund, matching `cam_percentageoffund`. numeric(6,3) so a three-way split can
  -- be recorded as 33.333 rather than forcing whole numbers that cannot total 100.
  proportion  numeric(6,3) NOT NULL CHECK (proportion > 0 AND proportion <= 100),
  -- When the member signed the nomination. `cam_nominationdate` is required by the vendor.
  nominated_on date,
  address     jsonb,
  note        text,
  created_by  uuid        REFERENCES auth.users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT beneficiaries_account_tenant_fkey
    FOREIGN KEY (tenant_id, account_id) REFERENCES platform.accounts (tenant_id, id)
    ON DELETE CASCADE,
  -- Nullable link + ON DELETE SET NULL: the column list (Postgres 15+) is required because
  -- tenant_id is NOT NULL and must not be nulled with it.
  CONSTRAINT beneficiaries_contact_tenant_fkey
    FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id)
    ON DELETE SET NULL (contact_id)
);

CREATE INDEX IF NOT EXISTS beneficiaries_account_idx ON crm.beneficiaries(account_id);
CREATE INDEX IF NOT EXISTS beneficiaries_tenant_idx  ON crm.beneficiaries(tenant_id);

-- RLS mirrors crm.kyc_checks: staff read within the tenant lineage, the client reads their own,
-- writes are service_role only (the firm write path runs on `firm_session`, as every other
-- contact write does). A nomination names a third party's identity, so it is FORCEd like the rest
-- of the client's record rather than left readable to any authenticated principal.
ALTER TABLE crm.beneficiaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm.beneficiaries FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS beneficiaries_read ON crm.beneficiaries;
CREATE POLICY beneficiaries_read ON crm.beneficiaries FOR SELECT TO authenticated
  USING (
    (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
    OR contact_id = ANY (auth.contact_scope())
  );
GRANT SELECT ON crm.beneficiaries TO authenticated;
GRANT ALL ON crm.beneficiaries TO service_role;

-- Append-only history of who changed what, the same trigger every other client-facing table gets.
DROP TRIGGER IF EXISTS beneficiaries_audit ON crm.beneficiaries;
CREATE TRIGGER beneficiaries_audit
  AFTER INSERT OR UPDATE OR DELETE ON crm.beneficiaries
  FOR EACH ROW EXECUTE FUNCTION audit.record();


-- ============================================================================
-- crm.kyc_checks gains `terms` -- the third tick on the Compliance card.
--
-- The card states three: identity verified, AML screened, terms accepted. The first two have been
-- `kyc_checks` kinds since 0026; the third had nowhere to live, because accepting terms is
-- currently a set of answers inside an onboarding case (`flows/onboarding_seed.py`'s five terms
-- declarations) and a case is not a record anybody reads a client's compliance position off.
--
-- Widening the CHECK rather than inventing a `crm.terms_acceptances` table: all three are the same
-- SHAPE of fact -- a check of a given kind, with a status, a date it was verified and a date it
-- expires -- and a firm reads them as one list. A separate table would need its own policy, its own
-- audit trigger and its own read on every surface that shows the trio.
--
-- NOTHING WRITES IT YET, and the card says so rather than implying a failure: an absent check
-- renders "not recorded", which is what it is. The onboarding flow's terms step is the natural
-- writer and is left for the change that wires it.
-- ============================================================================

ALTER TABLE crm.kyc_checks DROP CONSTRAINT IF EXISTS kyc_checks_kind_check;
ALTER TABLE crm.kyc_checks
  ADD CONSTRAINT kyc_checks_kind_check CHECK (kind IN ('identity', 'aml', 'terms'));


-- ============================================================================
-- crm.contact_notes gains a uid -- because a note is addressable now.
--
-- Notes have been append-only since 0002 and nothing ever named ONE of them, so the table never
-- needed a public handle. Deleting your own note is the first operation that addresses a single
-- row, and `docs/api.md` -> "Addressing" is unambiguous: `id` is internal and never appears in a
-- path. `tests/test_public_ids.py::test_no_api_path_names_a_serial` is the enforcement, and it
-- caught this rather than a reviewer.
--
-- Backfilled, then NOT NULL: every existing note gets one in the same statement, so there is no
-- window where a row is unaddressable. `nanoid(12)` is the estate's bare 12-character handle
-- (Decision 9) -- no type prefix, exactly as every other uid on the book.
-- ============================================================================

-- ONE STATEMENT, WITH THE DEFAULT, and the reason is FORCE ROW LEVEL SECURITY.
--
-- The obvious shape is 0111's -- add the column, `UPDATE ... WHERE uid IS NULL`, then SET NOT
-- NULL -- and on THIS table it fails: `crm.contact_notes` carries `FORCE ROW LEVEL SECURITY`, so
-- the policies bind the table's OWNER too. The migrator's UPDATE is therefore filtered to the rows
-- its claims can see (none: it sets no `request.jwt.claims`), reports `UPDATE 0`, and the
-- subsequent SET NOT NULL -- which validates every row, RLS or not -- fails with "contains null
-- values" against rows the same transaction was just told did not exist.
--
-- A defaulted ADD COLUMN has no such asymmetry: `nanoid` is VOLATILE, so Postgres rewrites the
-- table and evaluates the default once PER ROW, giving every existing note its own uid without
-- reading through a policy. 0111 gets away with the UPDATE because it runs where the rows are
-- visible; this is the shape to copy for any FORCE-RLS table.
--
-- The test suite could not have caught this: it builds each database from scratch, where
-- `crm.contact_notes` is empty and NOT NULL is satisfied trivially. It reproduces on any database
-- that has a single note in it.
ALTER TABLE crm.contact_notes
  ADD COLUMN IF NOT EXISTS uid text NOT NULL DEFAULT public.nanoid(12);
CREATE UNIQUE INDEX IF NOT EXISTS contact_notes_uid_key ON crm.contact_notes(uid);
