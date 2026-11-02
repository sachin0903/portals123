-- ============================================================================
-- 0035_link_tenant_integrity.sql — two halves of the same hole: a claim minter that answers
-- questions about other people, and client links that point outside their own row's firm.
--
-- ---------------------------------------------------------------------------
-- PART A — auth.resolve_claims(text, text) containment.
--
-- 0030 closed the `USING (true)` reads in `auth` and, in the same pass, established that a
-- BYPASSRLS SECURITY DEFINER function taking a caller-supplied argument is itself a cross-tenant
-- oracle and must guard its own argument (auth.tenant_by_workos_org / auth.tenant_lineage_of).
-- auth.resolve_claims is the LARGEST such accessor in the schema — it is the claim minter: given a
-- WorkOS user id and org id it returns the full pre-resolved claim set (tenant_id, tenant_ids,
-- portal, contact_ids, account_ids, role_ids) — and it was left unguarded. `authenticated` holds
-- EXECUTE (it must: db.py mints through it as the request role), so any session that reaches SQL
-- could call
--
--     SELECT auth.resolve_claims('<another firm''s adviser>', '<that firm''s org>')
--
-- and read back that adviser's ENTIRE BOOK — every contact id and account id they may see — from a
-- client-portal session of an unrelated firm. Two narrower spellings of the same oracle:
--
--   * (NULL, '<another firm''s org>') — the user lookup misses, so the function falls through to
--     `auth.tenants WHERE workos_organisation_id = p_workos_org_id`, handing back that firm's
--     tenant_id AND its descendant lineage. That is precisely the org→tenant resolution 0030's
--     auth.tenant_by_workos_org() was guarded to prevent, reachable by another route.
--   * ('<my own id>', '<another firm''s org>') — org drives the portal classification, so an
--     unmatched org silently promotes the answer to a `platform` (staff) context.
--
-- So the guard has to constrain BOTH arguments, not just the user id.
--
-- THE SHAPE. Revoking EXECUTE is not available (the request role is a legitimate caller) so, as in
-- 0030, the function guards itself:
--
--   * no claims published yet  -> allow. This is the mint case and the ONLY thing db.py does:
--     tenant_session / firm_session / admin_session all call resolve_claims BEFORE
--     `set_config('request.jwt.claims', ...)`, so `auth.jwt()` is '{}' at that moment.
--   * claims published         -> you may only ask about YOURSELF: p_workos_user_id must equal the
--     `sub` claim and p_workos_org_id must be NULL or equal the `org_id` claim.
--   * auth.is_system()         -> allow (Graphene ops, matching 0030's accessors and the policies).
--
-- WHY "no claims" IS NOT A TRIVIAL BYPASS. The one surface that executes caller-authored SQL as
-- `authenticated` is a firm-authored dataset query (app/render/resolve.py::_resolve_sql), and that
-- is exactly where this oracle was reachable: `SELECT auth.resolve_claims(...)` is a bare, STABLE,
-- read-only SELECT, so it walks straight through validate_dataset_sql. A query cannot get itself
-- into the mint branch by clearing the GUC: `set_config` is on _FORBIDDEN_FUNCTIONS in every
-- spelling (tests/test_datasets_sql_security.py) AND _resolve_sql re-reads the role and claims after
-- the statement and discards the rows if either moved. The mint branch is therefore unreachable from
-- the only authenticated SQL-execution surface in the product.
--
-- The residual is honest and recorded rather than papered over: a principal who can rewrite
-- `request.jwt.claims` at will is not bounded by ANY claims-derived guard — it can forge tenant_id
-- directly and skip resolve_claims altogether. The structural fix for that class is
-- `REVOKE EXECUTE ON FUNCTION set_config(text, text, boolean) FROM authenticated`, which needs a
-- superuser (pg_catalog's owner) and needs db.py::tenant_session to publish the claims BEFORE
-- `SET LOCAL ROLE authenticated` — resolve.py already orders it that way for this reason. Both are
-- outside this file; see the handoff.
--
-- ALSO (defence in depth, same function): resolve_claims resolved contact/account scope by
-- user_id/contact_id alone, with no tenant conjunct. A single mis-tenanted crm.contact_users or
-- crm.client_advisers row therefore put another firm's contact into a login's contact_ids claim —
-- the root of every `contact_scope()` policy branch below. Every scope lookup is now ANDed with the
-- resolved tenant, so the claim set cannot leave the firm the login belongs to. Behaviour-preserving
-- for correct data (which Part B now makes the only possible kind).
--
-- ---------------------------------------------------------------------------
-- PART B — a row's client/account link must live in the row's own firm.
--
-- The reported case (docs/audit, wave-2 verifier): documents.attachments / platform.instructions
-- WITH CHECK leaves contact_id unconstrained on the auth.can_read_all() branch, while the read
-- policies' client branch (`contact_id = ANY (auth.contact_scope())`) carries no tenant conjunct.
-- A read-all staffer at Firm A could therefore file a Firm A row stamped with Firm B's contact_id,
-- and Firm B's own client would then read that row through their own contact scope.
--
-- Patching those two WITH CHECKs would be a fix defeated by the first sibling table anyone looks at.
-- The same shape is everywhere: documents.document_contacts (the worst — it is what documents_read's
-- client branch joins through, so one poisoned link is a cross-firm DOCUMENT read),
-- documents.requests, documents.form_responses, tasks.tasks (its read-all WITH CHECK branch says
-- nothing about assignee_contact_id) — all `authenticated`-writable with a tenant-only WITH CHECK.
-- And a policy fix binds only `authenticated`: every one of these tables is also written by
-- service_role (BYPASSRLS) from firm_session/admin_session/the worker, where WITH CHECK does not
-- apply at all.
--
-- So this is fixed once, declaratively, as the invariant it actually is: a COMPOSITE FOREIGN KEY on
-- (tenant_id, <link_id>) referencing crm.contacts (tenant_id, id) / platform.accounts (tenant_id,
-- id). Properties that make this the right instrument rather than a policy edit:
--
--   * Referential integrity checks ALWAYS bypass row security and run with the table owner's
--     privileges, so the constraint is identical for `authenticated`, `service_role` and the
--     migrator. There is no role that can write a cross-tenant link.
--   * It is not a denylist and has no spellings to enumerate. It cannot be reached round.
--   * MATCH SIMPLE means a NULL link is simply unconstrained, which is the existing semantics for
--     every nullable column here (a firm-internal file, a standalone form response).
--   * ON UPDATE NO ACTION (the default) additionally blocks the other direction — you cannot move a
--     contact to another tenant while rows reference it.
--
-- The pre-existing single-column FKs are KEPT and each composite FK repeats their referential
-- action, so delete behaviour is unchanged (two constraints performing the same SET NULL / CASCADE
-- is idempotent). Dropping them by guessed auto-generated name would be the riskier edit.
--
-- With this in place the flagged WITH CHECKs and read policies are sound as written: attachments
-- .tenant_id = contact.tenant_id is now a schema fact, so `contact_id = ANY (auth.contact_scope())`
-- can no longer match a row from another firm. No policy in 0016/0018/0031 needs to change, and the
-- fix covers every sibling table in the same stroke.
--
-- DELIBERATELY NOT INCLUDED: documents.documents.account_id has never had ANY foreign key (0005
-- declares the column with a "FK added by platform_crm" comment that 0002 never honoured), so it may
-- already hold dangling ids and adding a validating constraint could fail the migration on live
-- data. It is also not part of any client-visibility branch (documents scope through
-- document_contacts, not account_scope). Recorded as a handoff, not silently skipped.
--
-- THE COVERAGE IS DERIVED, NOT LISTED. tests/test_schema_invariants.py::
-- test_every_client_link_is_tenant_contained reads pg_constraint and requires a composite FK for
-- EVERY column with a single-column FK to crm.contacts / platform.accounts, and a companion test
-- pins the "no FK at all" set by name. That is what makes this a rule rather than a sweep: it found
-- documents.documents.intended_contact_id (below) which reading the audit did not, and it will name
-- the next table someone gives a bare contact_id.
--
-- Expand-only: constraints and function bodies, no column or data change. lock_timeout per repo
-- convention (0024) — ADD CONSTRAINT takes a short ACCESS EXCLUSIVE / SHARE ROW EXCLUSIVE lock, so
-- fail fast rather than queue behind a long transaction.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- ============================================================================
-- PART A — auth.resolve_claims: containment guard + tenant-scoped scope lookups.
-- The body is 0027_tasks.sql's verbatim, plus the guard at the top and `AND tenant_id = v_tenant_id`
-- on each scope lookup. Everything the claims contract returns is unchanged.
-- ============================================================================
CREATE OR REPLACE FUNCTION auth.resolve_claims(p_workos_user_id text, p_workos_org_id text)
  RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = 'auth' AS $fn$
DECLARE
  v_user_uuid   uuid;
  v_tenant_id   int;
  v_tenant_ids  int[];
  v_portal      text;
  v_contact_ids int[];
  v_account_ids int[];
  v_role_ids    int[];
  v_claims      jsonb := auth.jwt();
BEGIN
  -- CONTAINMENT (0035, see the header). This function is BYPASSRLS and returns another principal's
  -- entire resolved scope, so it may only ever be asked about the caller. A session that has not yet
  -- published claims is the request layer minting them (the only in-tree caller); a session that HAS
  -- claims must be asking about itself; Graphene ops keep the cross-tenant view, as in 0030.
  -- BOTH arguments are constrained: the org id alone resolves a tenant + its lineage through the
  -- fallback below, and it also decides the portal classification.
  IF v_claims <> '{}'::jsonb AND NOT auth.is_system() AND NOT (
       p_workos_user_id IS NOT NULL
       AND p_workos_user_id = v_claims ->> 'sub'
       AND (p_workos_org_id IS NULL OR p_workos_org_id = v_claims ->> 'org_id')
     ) THEN
    RAISE EXCEPTION
      'auth.resolve_claims: a session with published claims may only resolve its own identity'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  SELECT id, tenant_id INTO v_user_uuid, v_tenant_id FROM auth.users
    WHERE workos_user_id = p_workos_user_id AND deleted_at IS NULL;

  IF v_tenant_id IS NULL THEN
    SELECT id INTO v_tenant_id FROM auth.tenants
      WHERE workos_organisation_id = p_workos_org_id;
  END IF;

  IF v_tenant_id IS NOT NULL THEN
    SELECT COALESCE(array_agg(id), '{}'::int[]) INTO v_tenant_ids
      FROM auth.tenants WHERE v_tenant_id = ANY(lineage);
  ELSE
    v_tenant_ids := '{}'::int[];
  END IF;

  -- Classify the active org -> portal (project-per-tenant: org = portal). Unknown/unmatched
  -- defaults to 'platform' (a staff context); the client portal is matched explicitly so it
  -- can never be widened. Operations goes through the admin (service_role) path, not RLS.
  SELECT CASE
           WHEN p_workos_org_id = te.client_portal_org_id THEN 'client'
           WHEN p_workos_org_id = te.operations_org_id     THEN 'operations'
           ELSE 'platform'
         END
    INTO v_portal
    FROM auth.tenant_environments te
    WHERE te.tenant_id = v_tenant_id
    ORDER BY te.id
    LIMIT 1;
  v_portal := COALESCE(v_portal, 'platform');

  -- Client scope. In the client portal: ONLY the parties this login is linked to
  -- (crm.contact_users) -- never an adviser book. Elsewhere (staff): the login's own links
  -- PLUS its assigned book (crm.client_advisers).
  --
  -- Every lookup is ANDed with the RESOLVED TENANT (0035): the claim set is the root of every
  -- `contact_scope()` / `account_scope()` policy branch, so a single mis-tenanted link row must not
  -- be able to put another firm's contact into it. Part B makes such a row impossible; this makes
  -- the minter independent of that.
  IF v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL THEN
    IF v_portal = 'client' THEN
      SELECT COALESCE(array_agg(DISTINCT contact_id), '{}'::int[]) INTO v_contact_ids
        FROM crm.contact_users WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id;
    ELSE
      SELECT COALESCE(array_agg(DISTINCT cid), '{}'::int[]) INTO v_contact_ids FROM (
        SELECT contact_id AS cid FROM crm.contact_users
          WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id
        UNION
        SELECT contact_id AS cid FROM crm.client_advisers
          WHERE adviser_id = v_user_uuid AND tenant_id = v_tenant_id
      ) s;
    END IF;
  ELSE
    v_contact_ids := '{}'::int[];
  END IF;

  IF array_length(v_contact_ids, 1) IS NOT NULL THEN
    SELECT COALESCE(array_agg(DISTINCT aid), '{}'::int[]) INTO v_account_ids FROM (
      SELECT id AS aid FROM platform.accounts
        WHERE contact_id = ANY(v_contact_ids) AND tenant_id = v_tenant_id
      UNION
      SELECT account_id AS aid FROM crm.account_contacts
        WHERE contact_id = ANY(v_contact_ids) AND account_id IS NOT NULL
          AND tenant_id = v_tenant_id
    ) s;
  ELSE
    v_account_ids := '{}'::int[];
  END IF;

  -- Roles this login actively holds for the resolved portal (drives role-assigned task
  -- filtering). Fail-closed empty; array-shaped even though today's memberships carry one
  -- role per (user, tenant, portal).
  IF v_user_uuid IS NOT NULL AND v_tenant_id IS NOT NULL THEN
    SELECT COALESCE(array_agg(DISTINCT role_id), '{}'::int[]) INTO v_role_ids
      FROM auth.organisation_memberships
      WHERE user_id = v_user_uuid AND tenant_id = v_tenant_id AND portal = v_portal
        AND status = 'active' AND role_id IS NOT NULL;
  ELSE
    v_role_ids := '{}'::int[];
  END IF;

  RETURN jsonb_build_object(
    'user_uuid',   v_user_uuid,
    'tenant_id',   v_tenant_id,
    'tenant_ids',  to_jsonb(v_tenant_ids),
    'portal',      v_portal,
    'contact_ids', to_jsonb(v_contact_ids),
    'account_ids', to_jsonb(v_account_ids),
    'role_ids',    to_jsonb(v_role_ids)
  );
END;
$fn$;

ALTER FUNCTION auth.resolve_claims(text, text) OWNER TO service_role;

COMMENT ON FUNCTION auth.resolve_claims(text, text) IS
  'The claim minter (SECURITY DEFINER, service_role/BYPASSRLS). CONTAINED (0035): a session with '
  'published request.jwt.claims may only resolve its OWN identity (sub + org_id); the unguarded '
  'form let any authenticated session read back another firm''s adviser''s whole book. The '
  'no-claims branch is the request layer minting (db.py calls this BEFORE set_config).';

-- ============================================================================
-- PART B — composite (tenant_id, link) foreign keys.
--
-- The referenced unique keys. `id` is already the primary key of both tables, so (tenant_id, id) is
-- trivially unique; the constraint exists only to give the composite FKs something to point at.
-- Small tables (dev/staging scale), so a plain ADD CONSTRAINT rather than the
-- CREATE UNIQUE INDEX CONCURRENTLY + ADD CONSTRAINT USING INDEX dance.
-- ============================================================================
ALTER TABLE crm.contacts       ADD CONSTRAINT contacts_tenant_id_key  UNIQUE (tenant_id, id);
ALTER TABLE platform.accounts  ADD CONSTRAINT accounts_tenant_id_key  UNIQUE (tenant_id, id);

-- ---- links to crm.contacts (tenant_id, id) -------------------------------------------------
-- The two tables that DEFINE contact scope. A mis-tenanted row here is the sharpest edge of all:
-- resolve_claims reads them to build contact_ids, so it would hand a login another firm's client.
ALTER TABLE crm.contact_users ADD CONSTRAINT contact_users_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id) ON DELETE CASCADE;
ALTER TABLE crm.client_advisers ADD CONSTRAINT client_advisers_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id) ON DELETE CASCADE;

ALTER TABLE crm.contact_notes ADD CONSTRAINT contact_notes_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id) ON DELETE CASCADE;
ALTER TABLE crm.relationships ADD CONSTRAINT relationships_from_contact_tenant_fkey
  FOREIGN KEY (tenant_id, from_contact_id) REFERENCES crm.contacts (tenant_id, id) ON DELETE CASCADE;
ALTER TABLE crm.relationships ADD CONSTRAINT relationships_to_contact_tenant_fkey
  FOREIGN KEY (tenant_id, to_contact_id) REFERENCES crm.contacts (tenant_id, id) ON DELETE CASCADE;
ALTER TABLE crm.kyc_checks ADD CONSTRAINT kyc_checks_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id) ON DELETE CASCADE;

-- Nullable link + ON DELETE SET NULL: the column list (Postgres 15+) is required because tenant_id
-- is NOT NULL and must not be nulled with it.
ALTER TABLE crm.account_contacts ADD CONSTRAINT account_contacts_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id)
  ON DELETE SET NULL (contact_id);
ALTER TABLE platform.accounts ADD CONSTRAINT accounts_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id) ON DELETE RESTRICT;
ALTER TABLE platform.transactions ADD CONSTRAINT transactions_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id)
  ON DELETE SET NULL (contact_id);

-- The reported pair (documents.attachments / platform.instructions) ...
ALTER TABLE documents.attachments ADD CONSTRAINT attachments_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id)
  ON DELETE SET NULL (contact_id);
ALTER TABLE platform.instructions ADD CONSTRAINT instructions_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id)
  ON DELETE SET NULL (contact_id);

-- ... and the siblings that would otherwise have been the bypass. document_contacts is the one that
-- turns a poisoned link into a cross-firm document read (documents_read joins through it).
ALTER TABLE documents.document_contacts ADD CONSTRAINT document_contacts_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id) ON DELETE CASCADE;
-- documents.documents.intended_contact_id (0021) is described there as "read by no RLS policy",
-- which is true and not the whole story: app/worker.py reads it to decide which client a queued
-- render is FOR, and that contact becomes the render's `:subject_id`. A cross-tenant value would
-- therefore resolve another firm's client into a generated document. Contained like the rest.
ALTER TABLE documents.documents ADD CONSTRAINT documents_intended_contact_tenant_fkey
  FOREIGN KEY (tenant_id, intended_contact_id) REFERENCES crm.contacts (tenant_id, id)
  ON DELETE SET NULL (intended_contact_id);
ALTER TABLE documents.requests ADD CONSTRAINT requests_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id) ON DELETE CASCADE;
ALTER TABLE documents.form_responses ADD CONSTRAINT form_responses_contact_tenant_fkey
  FOREIGN KEY (tenant_id, contact_id) REFERENCES crm.contacts (tenant_id, id)
  ON DELETE SET NULL (contact_id);
ALTER TABLE flows.cases ADD CONSTRAINT cases_subject_contact_tenant_fkey
  FOREIGN KEY (tenant_id, subject_contact_id) REFERENCES crm.contacts (tenant_id, id)
  ON DELETE SET NULL (subject_contact_id);
ALTER TABLE tasks.tasks ADD CONSTRAINT tasks_assignee_contact_tenant_fkey
  FOREIGN KEY (tenant_id, assignee_contact_id) REFERENCES crm.contacts (tenant_id, id)
  ON DELETE CASCADE;

-- ---- links to platform.accounts (tenant_id, id) --------------------------------------------
-- account_scope() is the other half of client visibility (platform.instructions' read policy
-- matches on it), and it is derived from the contacts above, so the account link needs the same
-- containment.
ALTER TABLE crm.account_contacts ADD CONSTRAINT account_contacts_account_tenant_fkey
  FOREIGN KEY (tenant_id, account_id) REFERENCES platform.accounts (tenant_id, id)
  ON DELETE SET NULL (account_id);
ALTER TABLE platform.transactions ADD CONSTRAINT transactions_account_tenant_fkey
  FOREIGN KEY (tenant_id, account_id) REFERENCES platform.accounts (tenant_id, id)
  ON DELETE SET NULL (account_id);
ALTER TABLE platform.instructions ADD CONSTRAINT instructions_account_tenant_fkey
  FOREIGN KEY (tenant_id, account_id) REFERENCES platform.accounts (tenant_id, id)
  ON DELETE CASCADE;
ALTER TABLE flows.case_steps ADD CONSTRAINT case_steps_account_tenant_fkey
  FOREIGN KEY (tenant_id, account_id) REFERENCES platform.accounts (tenant_id, id)
  ON DELETE SET NULL (account_id);
