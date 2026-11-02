-- ============================================================================
-- 0136_provider_users.sql -- sync a custodian's own employee users into `auth.users`:
-- ATTACH to the login that already holds the email, otherwise INSERT one, inactive.
--
-- ---------------------------------------------------------------------------
-- THIS REVERSES 0118's "a custodian's own people are NOT rows in this table", DELIBERATELY,
-- AND THE MERGE RULE IS WHAT MAKES IT SAFE.
-- ---------------------------------------------------------------------------
--
-- 0118 argued the decisive objection was `UNIQUE (tenant_id, email)`: an adviser who is BOTH a
-- Graphene login and an SEI user has ONE email, so syncing their SEI record violates it "for
-- exactly the people the mapping exists for", and "the sync would have to detect the collision
-- and merge, which IS the mapping, only implicit and with nobody confirming it."
--
-- The collision detection is now the SPECIFIED behaviour rather than an accident: a colliding
-- email is the SIGNAL, and attaching `provider_refs` to that row is the mapping being made.
-- What was rejected was an implicit merge nobody could see; what this does is state the rule in
-- one place, in the database, where it cannot drift per provider.
--
-- It writes NO reject ledger — see the note beside the CTEs. Three reasons were tried and each
-- was answerable somewhere better: two were states the schema now forbids or the projection
-- filters, and the third is a skip that leaves both sides readable.
--
-- The bar for an automatic attach is `aml.match_contact`'s bar: an EXACT email match, and
-- exactly one of them — which is a DATABASE GUARANTEE rather than a check in here. The address is
-- STORED folded (`users_email_is_folded`, 0125) and `users_tenant_email_key` (0001) is unique per
-- tenant, so the ambiguous state this function used to refuse — `A.Smith@firm.test` and
-- `a.smith@firm.test` as two logins for one person, reachable because nothing folded the case on
-- any write path — cannot exist. The cause was fixed instead of the symptom, and the guard, its
-- reject reason and its test were removed with it.
--
-- Two refusals remain, and neither is about case: one person carrying two ids at one custodian,
-- and a user the provider holds no address for at all.
--
-- ---------------------------------------------------------------------------
-- WHAT IS AND IS NOT SYNCED, MEASURED FROM THE p04 DROP (2026-03-27).
-- ---------------------------------------------------------------------------
--
-- `UserDetail` is 1,591 rows, and syncing all of them would be wrong. SEI's `USER_TYPE` is a
-- Reference code set with exactly three members, and the drop splits:
--
--   1  Employee            706    <-- the only ones synced
--   2  End Client          865        INVESTORS. A client, not staff.
--   3  Data Integration     20        service accounts (SFTP/API machine logins)
--
-- Syncing type 2 would put 865 investors into a firm's STAFF list, and — far worse — an End
-- Client's email plausibly matches the login a client already holds for the client portal, so
-- the attach rule would bind a custodian's STAFF-shaped id onto a client's identity. Type 3 are
-- machines, which are not people at all. So the provider layer publishes Employee ONLY and the
-- exclusion is stated where the population is read, not here.
--
-- That scope is CORROBORATED rather than assumed: of the 233 distinct `ACCOUNT_DSR_EMP_ID`
-- values in `ClientAccountLink`, the 37 that appear in `UserDetail` at all are **37 of 37
-- Employee**. The type this syncs is exactly the type an account's servicing relationship names.
--
-- Departed staff ARE synced (470 of the 706 are already `USER_STATUS = 'N'` at SEI): a
-- departed adviser is still the name on an account's history, so dropping them would leave
-- those accounts attributed to nobody. They land `enabled = false` like everyone else here.
--
-- ---------------------------------------------------------------------------
-- WHY AN INSERTED ROW IS VISIBLE BUT POWERLESS, AND HOW.
-- ---------------------------------------------------------------------------
--
-- 0118's second objection was that `auth_users_scoped_read` (0001) gates on an
-- `auth.organisation_memberships` row, so a synced user with none "is invisible even to its own
-- firm's staff — the picker could not read them", and inventing a membership "is worse: that
-- table is what grants portal access".
--
-- Both halves are true and they are not in tension, because the two policies read DIFFERENT
-- columns. `auth_users_scoped_read` requires a membership row and does NOT look at its status;
-- `auth.resolve_claims` (0107) grants a portal only `AND m.status = 'active'`, and resolves
-- roles under the same condition. So a membership row with `status = 'inactive'` makes the
-- person READABLE to their own firm's staff while granting no portal, no role and no claim —
-- which is exactly "visible but powerless", and it is enforced by the existing policies rather
-- than by a new one.
--
-- `enabled = false` is the second, independent floor, and it is this table's OWN existing
-- meaning of inactive ("invited users are created disabled until acceptance", 0001). A row here
-- also has NO `workos_user_id`, so there is no identity to authenticate even if both were
-- flipped: WorkOS is the only issuer of a session.
--
-- ---------------------------------------------------------------------------
-- WHO OWNS WHICH COLUMN AFTERWARDS -- the rule that keeps an ATTACH non-destructive.
-- ---------------------------------------------------------------------------
--
-- An ATTACH must never overwrite a real login's own facts. WorkOS owns the email and names of a
-- provisioned person and the firm owns the 0118 profile columns, so on a row that has a
-- `workos_user_id` the provider owns **`provider_refs` and nothing else**.
--
-- On a row this function INSERTED there is no other source, so the provider owns the display
-- fields too and refreshes them — a corrected surname at the custodian should reach the picker.
-- The discriminator is `workos_user_id IS NULL`, and it is derived rather than stored for the
-- reason `client_products` was not built: a column duplicating a derivable fact is a second
-- spelling that can disagree with the first.
-- ============================================================================

CREATE FUNCTION auth.merge_provider_user(
  p_source    regclass,  -- inbound.{uid}__users: one row per provider employee user
  p_tenant_id int,
  p_provider  text
)
RETURNS TABLE (inserted bigint, updated bigint)
LANGUAGE plpgsql
-- SECURITY DEFINER, unlike every generic merge, because `book_writer` deliberately holds NO
-- privilege on `auth.users` (0062 grants it `SELECT` on `auth.tenants` and nothing else in that
-- schema) and this migration does not give it one. The pipeline gets EXECUTE on this function
-- and therefore exactly these three statements against that table — it cannot read a password
-- hash, cannot disable a real login, cannot write any other column. Widening the grant to the
-- table would have handed it all of them.
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_sql       text;
  v_inserted  bigint := 0;
  v_attached  bigint := 0;
  v_refreshed bigint := 0;
BEGIN
  -- Dynamic only because the landing table's name carries the tenant uid; every value is bound.
  --
  -- ONE statement per outcome, in this order, and the order matters: a row already mapped must
  -- not be re-attached to a different person, and a row attachable by email must not also be
  -- inserted (which `users_tenant_email_key` would refuse anyway, failing the whole entity).
  v_sql := format($fmt$
    WITH staged AS (
      SELECT nullif(btrim(cast(s.external_id AS text)), '')  AS external_id,
             -- FOLDED here, once, so every comparison below is plain equality against a column
             -- that is itself stored folded (`users_email_is_folded`, 0125). The custodian's
             -- spelling of an address need not match the identity provider's.
             nullif(lower(btrim(cast(s.email AS text))), '')  AS email,
             nullif(btrim(cast(s.first_name  AS text)), '')  AS first_name,
             nullif(btrim(cast(s.last_name   AS text)), '')  AS last_name,
             nullif(btrim(cast(s.phone       AS text)), '')  AS phone
        FROM %1$s s
       WHERE nullif(btrim(cast(s.external_id AS text)), '') IS NOT NULL
    ),
    -- Already mapped: this provider id is on a row. Nothing to decide.
    mapped AS (
      SELECT st.*, u.id AS user_id, u.workos_user_id
        FROM staged st
        JOIN auth.users u
          ON u.tenant_id = $1
         AND u.provider_refs ->> $2 = st.external_id
    ),
    -- Attachable: not mapped, has an email, and a login in this tenant carries it.
    --
    -- AT MOST ONE CAN, and that is a database guarantee rather than something checked here:
    -- 0132 stores the email FOLDED and `users_email_is_folded` enforces it, so `lower(email)`
    -- and the stored value are the same string and `users_tenant_email_key` (0001) already makes
    -- it unique per tenant. (An earlier cut of this comment credited an index,
    -- `users_tenant_email_ci_key`, that no upgrade path creates.) An
    -- earlier cut of this function aggregated the matches and refused `count(*) > 1` as ambiguous,
    -- because the only key then was case-SENSITIVE and one tenant could hold two case-variant
    -- rows for one person. Folding the key removed the state, so the guard went with it — a
    -- condition the schema forbids does not need a second, weaker statement in here.
    candidates AS (
      SELECT st.external_id, st.email, u.id AS user_id
        FROM staged st
        JOIN auth.users u
          -- Plain equality: both sides are folded (the column by constraint, the staged value in
          -- `staged` above), so this is an index seek on `users_tenant_email_key` rather than a
          -- scan over `lower()`.
          ON u.tenant_id = $1 AND u.email = st.email
       WHERE st.email IS NOT NULL
         AND st.external_id NOT IN (SELECT external_id FROM mapped)
    ),
    attachable AS (
      SELECT c.*
        FROM candidates c
        JOIN auth.users u ON u.id = c.user_id
         -- A row already naming a DIFFERENT id for this provider is one person with two custodian
         -- identities — the shape two employee records sharing one address makes. SKIPPED, not
         -- overwritten: whatever is there is either an operator's own mapping or an earlier run's,
         -- and a nightly job is not the thing that should change its mind about which.
       WHERE (u.provider_refs ->> $2) IS NULL
    ),
    attached AS (
      UPDATE auth.users u
         SET provider_refs = coalesce(u.provider_refs, '{}'::jsonb)
                               || jsonb_build_object($2, a.external_id)
        FROM attachable a
       WHERE u.id = a.user_id
      RETURNING 1
    ),
    -- Refresh the display fields of rows THIS function created, and only those.
    refreshed AS (
      UPDATE auth.users u
         SET first_name = coalesce(m.first_name, u.first_name),
             last_name  = coalesce(m.last_name,  u.last_name),
             mobile     = coalesce(m.phone,      u.mobile)
        FROM mapped m
       WHERE u.id = m.user_id
         AND m.workos_user_id IS NULL
         AND (u.first_name, u.last_name, u.mobile)
             IS DISTINCT FROM
             (coalesce(m.first_name, u.first_name),
              coalesce(m.last_name,  u.last_name),
              coalesce(m.phone,      u.mobile))
      RETURNING 1
    ),
    -- Everything else is new. `enabled` takes the column default (false) rather than being
    -- stated, the rule 0067 records: a NOT NULL column the provider left empty must take the
    -- COLUMN'S OWN default.
    minted AS (
      INSERT INTO auth.users
        (tenant_id, email, first_name, last_name, mobile, provider_refs, type)
      SELECT $1,
             st.email,
             st.first_name,
             st.last_name,
             st.phone,
             jsonb_build_object($2, st.external_id),
             -- STAFF (0124): these are the firm's own people at its custodian. `is_adviser`
             -- (0125) takes its column default of false and is NEVER set here — no custodian can
             -- say who advises. Measured for all three: SEI's `DSR_TYPE_NAME` has seven 'Adviser'
             -- codes and ZERO accounts use one, Seccl's `userType` is `Machine | User`, and
             -- Morgan Lloyd names a single adviser contact per firm. An operator states it.
             ARRAY['staff']
        FROM staged st
       WHERE st.email IS NOT NULL
         AND st.external_id NOT IN (SELECT external_id FROM mapped)
         AND st.external_id NOT IN (SELECT external_id FROM candidates)
      -- Concurrent-safe rather than merely tidy: two firms enabled on the same SEI firm reach
      -- the same employee, and a second run must be a no-op (the idempotence
      -- `platform.links`' audit trigger depends on).
      ON CONFLICT (tenant_id, email) DO NOTHING
      RETURNING id
    ),
    -- The membership that makes a minted row READABLE to its own firm without granting it
    -- anything -- see the header. `platform` is the portal a staff-shaped person would hold;
    -- `inactive` is what withholds it.
    seen AS (
      INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, status)
      SELECT m.id, $1, 'platform', 'inactive' FROM minted m
      ON CONFLICT (user_id, tenant_id, portal) DO NOTHING
      RETURNING 1
    )
    -- NO REJECT LEDGER. This function writes `platform.merge_rejects` not at all, which makes it
    -- the one merge in the estate that files nothing — deliberately, and it took three passes to
    -- get here because each reason turned out to be answerable somewhere better:
    --
    --   * `email matches more than one login` -- the STATE was deleted rather than the symptom.
    --     The address is stored folded and unique per tenant (0125), so two case-variant logins
    --     for one person cannot exist.
    --   * `no email at the provider` -- filtered by `Entity.from_sql` before the staging model
    --     delivers a row, and answerable as `tenant_{uid}.users WHERE email IS NULL`.
    --   * `login already mapped to a different id` -- SKIPPED, below, by `attachable`'s own
    --     `IS NULL` test.
    --
    -- **A SKIP IS NOT A LOSS OF INFORMATION HERE, which is the whole reason it is allowed to be
    -- silent.** Every one of these is a statement about the CURRENT rows and both sides of it are
    -- readable: `platform.users` publishes the custodian's whole employee population in the firm's
    -- own slice, and `auth.users.provider_refs` publishes what is actually mapped. Anyone asking
    -- "which of this firm's SEI staff are not mapped, and why" answers it by comparing the two,
    -- for the reason `platform.reconciliation_breaks` was never built: a stored copy of a
    -- derivable state goes stale the moment either side is corrected — and this one would have
    -- gone stale nightly, filing the same rows again for ever until somebody stopped reading them.
    --
    -- What it does NOT do is overwrite. A login already naming a different id at this provider
    -- keeps it, so an operator's own mapping survives every run; the run simply declines to have
    -- an opinion about it.
    SELECT (SELECT count(*) FROM minted)::bigint,
           (SELECT count(*) FROM attached)::bigint,
           (SELECT count(*) FROM refreshed)::bigint
  $fmt$, p_source);

  EXECUTE v_sql USING p_tenant_id, p_provider
    INTO v_inserted, v_attached, v_refreshed;

  -- An ATTACH is an update to the book, so it is counted as one: the ledger's question is how
  -- many rows this run changed, and an attach changes exactly one.
  RETURN QUERY SELECT coalesce(v_inserted, 0::bigint),
                      coalesce(v_attached, 0::bigint) + coalesce(v_refreshed, 0::bigint);
END;
$$;

-- Owned by service_role, because a SECURITY DEFINER function runs as its OWNER and this one
-- must reach `auth.users`. `migrator` is not a member of `service_role`, so a function left in
-- its ownership would run with the migration role's rights in production — the trap
-- `docs/api.md` records for `auth.resolve_claims`.
ALTER FUNCTION auth.merge_provider_user(regclass, int, text) OWNER TO service_role;

-- The pipeline may CALL it and still holds nothing on the table itself.
REVOKE ALL ON FUNCTION auth.merge_provider_user(regclass, int, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.merge_provider_user(regclass, int, text)
  TO book_writer, service_role;

COMMENT ON FUNCTION auth.merge_provider_user(regclass, int, text) IS
  'Sync a custodian''s employee users into auth.users: attach provider_refs to the single login '
  'already holding the email, else insert one disabled with an inactive platform membership '
  '(readable by the firm, granted nothing). Ambiguous or already-mapped cases go to '
  'Ambiguous cases are SKIPPED rather than resolved by guessing or filed as rejects — every one '
  'is derivable by comparing platform.users against provider_refs, and an operator''s own mapping '
  'is never overwritten. Returns (inserted, changed).';
