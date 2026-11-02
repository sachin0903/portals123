-- ============================================================================
-- 0131_staff_profile.sql -- what a firm SAYS about its staff, and which id each custodian knows
-- them by. Both on `auth.users`, deliberately.
--
-- The client portal's adviser card has three looks (`adviser-card`, `adviser-portrait`,
-- `adviser-strip`) and all three drew an initials disc. That was never a design choice: `auth.users`
-- carried an email, a name, a mobile and a WorkOS `avatar`, so a firm could not state an adviser's
-- job title, their qualifications, a bio or a headshot. Seccl's illustration payload asks for an
-- `adviserInfo.FCANumber` by name and `pkgs/seccl` records that it has no source here.
--
-- ---------------------------------------------------------------------------
-- WHY THESE ARE COLUMNS AND NOT A SEPARATE PROFILE TABLE.
-- ---------------------------------------------------------------------------
--
-- A `crm.users` table was written first and reverted, and the reason it was written was wrong: the
-- argument was that `auth.users` is a WorkOS projection the sync overwrites, so a firm's own words
-- would sit in a table whose contract is "the IdP is authoritative". **The sync does not do that.**
-- Every `UPDATE auth.users SET` in the estate names its columns — email, first/last name, avatar,
-- workos_user_id, enabled, the invitation timestamps — so a column added here is never touched by
-- it. `avatar` is already display data on this table, which is the precedent.
--
-- The second argument was structural and is really about a different table: `platform.links.entity_id`
-- is `bigint` and `auth.users.id` is a `uuid`, so a login cannot be addressed by the provider-mapping
-- table. True — and irrelevant once the provider's id is a COLUMN here, which is exactly what
-- `workos_user_id` already is for the IdP.
--
-- What the separate table cost was a second RLS policy, a backfill, a sync chokepoint, a tripwire
-- test, and three tables with "user" in the name (`auth.users`, `crm.users`, `crm.contact_users`).
--
-- ---------------------------------------------------------------------------
-- AND WHY A CUSTODIAN'S OWN PEOPLE ARE **NOT** ROWS IN THIS TABLE.
--
-- >>> SUPERSEDED BY 0123, DELIBERATELY. READ THAT MIGRATION BEFORE ACTING ON THIS SECTION. <<<
--
-- They ARE rows now: `auth.merge_provider_user` (0123) syncs a custodian's employee users,
-- ATTACHING the provider's id to the login that already holds the same email and otherwise
-- INSERTING one disabled. The three objections below were not wrong so much as unanswered, and
-- 0123 answers each — the section is kept in full rather than deleted because the objections are
-- exactly what its design has to satisfy, and a future reader deciding whether to loosen any of
-- it needs to know what was at stake:
--
--   1. THE UNIQUE KEY. Answered by making the collision the SIGNAL rather than an accident: a
--      matching email means attach. What was rejected here was an IMPLICIT merge with nobody
--      confirming it; 0123 states the rule in the database, requires an exact match and exactly
--      one of them (`aml.match_contact`'s bar), and records every ambiguous case in
--      `platform.merge_rejects` for a human instead of resolving it.
--   2. THE READ POLICY. Answered by the two policies reading different columns:
--      `auth_users_scoped_read` requires a membership and does NOT look at its status, while
--      `auth.resolve_claims` grants a portal only `AND m.status = 'active'`. So an INACTIVE
--      membership makes the person readable to their own firm and grants nothing — "visible but
--      powerless", enforced by the policies that already exist. Inventing an ACTIVE one would
--      still be wrong, exactly as this section says.
--   3. VOLUME. Answered by measuring it properly, and the figure below is misleading: 1,591 is
--      the whole `UserDetail` report, and `USER_TYPE` splits it 706 Employee / 865 End Client /
--      20 Data Integration. Only Employee is synced — publishing the End Clients would have put
--      investors in a firm's staff list and let the attach rule bind a custodian's staff id onto
--      a client's own portal login. The members list orders the firm's own people first and tags
--      the rest.
--
-- The one claim below that stands unaltered is the MATCHING rule: never by display name.
-- ---------------------------------------------------------------------------
--
-- The tempting alternative is to sync each provider's users in as inactive logins and let the
-- mapping be "which row". Three things stop it, and the first is the one that decides it — and it
-- is decisive BECAUSE the email is available, not because it is missing:
--
-- 1. **`UNIQUE (tenant_id, email)`.** SEI's own API gives an email —
--    `GET /employeeservice/firmemployeeusers` returns `emailId` alongside `uniqueUserId`, a
--    structured name and a phone (the nightly `UserDetail` report does not: twelve columns, 1,591
--    rows, no address, and one `USER_DISPLAY_NAME` field holding "Armasoiu, Maria"). So the
--    NOT NULL is satisfiable. What is not satisfiable is the uniqueness: an adviser who is BOTH a
--    Graphene login and an SEI user has ONE email, so syncing their SEI record in violates that
--    constraint — for exactly the people the mapping exists for. The sync would have to detect the
--    collision and merge, which IS the mapping, only implicit and with nobody confirming it.
-- 2. **The read policy gates on a MEMBERSHIP.** `auth_users_scoped_read` (0001) requires an
--    `auth.organisation_memberships` row in the caller's lineage, so a synced provider user with no
--    membership is invisible even to its own firm's staff — the picker could not read them.
--    Inventing a membership is worse: that table is what grants portal access.
-- 3. **Volume.** 1,591 provider users against a handful of real staff, and both
--    `provisioning.members_with_portals` (the `/firm/members` list and the adviser picker) and
--    `contacts.py`'s adviser seeding enumerate `auth.users` per tenant.
--
-- They need no storing anyway: the option list is read live from the provider — SEI's is
-- `firmemployeeusers`, paged and scoped by processing org plus servicing relationship, which is the
-- same `servicingRelationshipId` + `employeeEntityId` pair enrolment takes. So the mapping is one
-- value per provider per person: `provider_refs` below.
--
-- (0123 keeps `provider_refs` as the mapping — that part is unchanged and is what an attach
-- writes. What changed is that the population is ALSO stored, so a firm can see who exists at its
-- custodian and an operator has something to map onto without a live call. Both of the vendor's
-- scopes turned out to be OPTIONAL, so it is one paged sweep rather than a request per firm per
-- servicing relationship, and every row states its own `firmId`.)
--
-- AND THE EMAIL RAISES THE FLOOR ON MATCHING. "Never inferred" was written against display NAMES,
-- and it stands: only 37 of the 233 distinct `ACCOUNT_DSR_EMP_ID` values in ClientAccountLink
-- appear in `UserDetail` at all, so a name match would be wrong 84% of the time. An EMAIL is an
-- identity rather than a label, so an exact match on one — and exactly one, which is the bar
-- `aml.match_contact` already sets for a person — is a defensible PROPOSAL for an operator to
-- accept. A miss stays ordinary: a firm's SEI address and its Graphene login address are often
-- different, so "no proposal" is the common case and never an error.
-- ============================================================================

ALTER TABLE auth.users
  -- What the FIRM says about the person. Nothing here comes from WorkOS and nothing here is
  -- touched by the sync.
  ADD COLUMN IF NOT EXISTS job_title      text,
  -- A LIST, because they compound: DipPFS and Chartered FCSI and CFP are three designations one
  -- person holds, and a single text field would be somebody's comma habit.
  ADD COLUMN IF NOT EXISTS qualifications text[],
  -- The FCA's own reference for the individual. Seccl's illustration payload asks for it by name.
  ADD COLUMN IF NOT EXISTS fca_reference  text,
  ADD COLUMN IF NOT EXISTS bio            text,
  -- The firm's own headshot. `avatar` mirrors WorkOS's profile picture and is read by nothing;
  -- this is the one a firm controls, and the reader falls back to that.
  ADD COLUMN IF NOT EXISTS photo_url      text,
  -- A DIRECT LINE, which is NOT `mobile`: that is the number WorkOS sends security codes to, and
  -- putting a desk number there would change where a login's MFA code goes.
  ADD COLUMN IF NOT EXISTS direct_phone   text,
  -- WHICH ID EACH CUSTODIAN KNOWS THIS PERSON BY: `{"sei": "57817496", "morgan_lloyd": "<guid>"}`.
  --
  -- A map rather than a table, for the reason a one-to-few relationship read whole with its parent
  -- always is here (`accounts.bank_accounts`, `accounts.payments`): a person holds at most one id
  -- per provider, nothing queries INTO it, and every reader wants it alongside the row.
  --
  -- SEI's is `UserDetail.ENTITY_ID`, which enrolment takes as
  -- `employeeServicingRelationshipList[].employeeEntityId`. **Set by an operator, never inferred
  -- from a name**: only 37 of the 233 distinct `ACCOUNT_DSR_EMP_ID` values in SEI's
  -- ClientAccountLink appear in `UserDetail` at all — the rest are adviser PARTIES rather than SEI
  -- logins — so matching on display name would be wrong 84% of the time.
  ADD COLUMN IF NOT EXISTS provider_refs  jsonb;

-- An OBJECT of provider -> id, or nothing. An array or a bare string would read as a mapping and
-- then fail every `->>` in the resolver, which is the shape of a bug nobody notices until a call
-- is sent naming the wrong person.
ALTER TABLE auth.users
  ADD CONSTRAINT users_provider_refs_is_object
  CHECK (provider_refs IS NULL OR jsonb_typeof(provider_refs) = 'object');

COMMENT ON COLUMN auth.users.job_title IS
  'What the FIRM says about this person, as distinct from what WorkOS says. The sync names its own '
  'columns and never touches this one.';
COMMENT ON COLUMN auth.users.qualifications IS
  'Designations the person holds (DipPFS, APFS, Chartered FCSI). A list because they compound.';
COMMENT ON COLUMN auth.users.direct_phone IS
  'A direct line. NOT `mobile`, which is the number WorkOS sends security codes to.';
COMMENT ON COLUMN auth.users.provider_refs IS
  'provider -> that custodian''s own id for this person, e.g. {"sei": "57817496"}. SEI''s is '
  'UserDetail.ENTITY_ID, which enrolment takes as employeeServicingRelationshipList[].employeeEntityId. '
  'Set by an operator and never inferred: only 37 of SEI''s 233 DSR employee ids are SEI logins.';
