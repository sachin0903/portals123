-- ============================================================================
-- 0031_book_scope_alignment.sql — bring every client-bearing table onto the ONE book-scoping
-- idiom, so "a book-scoped adviser sees only their own clients' records" holds everywhere.
--
-- The idiom (crm.contacts 0002:457, documents.documents 0021_documents_scope, tasks.tasks
-- 0028_tasks_polish) is three branches:
--
--   (a) full-book staff  — auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage())
--   (b) book-scoped staff — tenant_id = ANY (auth.tenant_lineage()) AND the row's client link is
--                           either absent (firm-internal work) or inside auth.contact_scope()
--   (c) the client       — the row's client link is in auth.contact_scope() (lineage is empty in
--                           the client portal, so (a)/(b) can never fire there)
--
-- And, for FOR ALL write policies, BOTH clauses must repeat the read scoping:
--
--   * USING — for SELECT Postgres ORs every applicable policy's USING together, so a tenant-wide
--     `FOR ALL ... USING` silently re-opens everything the read policy tightened (the trap
--     0021/0022 already fixed for documents/requests).
--   * WITH CHECK — "the app validates the target first" is NOT a substitute. An out-of-book INSERT
--     is not merely invisible to its creator: documents.shares becomes a live public download link
--     (GET /share/{token} resolves on service_role and checks only token + expiry), and
--     documents.attachments/platform.instructions become rows attributed to a client the writer
--     may not see. Every WITH CHECK below therefore repeats the read predicate, ANDed with tenant
--     containment so no branch can ever stamp another firm's tenant_id.
--
-- WITH CHECK deliberately keeps `tenant_id = ANY (auth.tenant_lineage())` as a conjunct on the
-- staff tables (shares / attachments / instructions): a client-portal session has an empty lineage
-- and has never been able to INSERT into them (0016/0017/0018 all required lineage on write, and
-- the client surfaces run on firm_session/service_role), so keeping it is behaviour-preserving as
-- well as strictly tighter. tasks.tasks is the one table a client legitimately writes under
-- `authenticated` (completing an action item), so its client branch is kept and gains an explicit
-- own-tenant conjunct instead.
--
-- What this migration fixes (docs/audit/06-core.md "Book-scoping consistency"):
--
--   1. crm.kyc_checks (0026:43) — tenant-wide. Identity/AML verification outcomes for every
--      client of the firm, readable by any staffer. The most sensitive table in the schema.
--   2. flows.cases + 3. flows.case_steps (0021_flows:151-171) — tenant-wide, so a book-scoped
--      adviser read every client's onboarding case and every step's detail, while the DOCUMENTS
--      attached to those same cases were correctly book-scoped.
--   4. flows.executions + 5. flows.domain_events (0025:106-113) — same, one level up: the
--      version-pinned run and its append-only fact log (whose payloads carry step/client detail).
--   6. documents.shares (0017:37-41) — tenant-wide WITH FULL WRITE, so a book-scoped adviser
--      could list AND revoke public share links for documents they cannot see — and, worse, MINT
--      one: an INSERT naming an out-of-book document's uid produced a working public download link
--      even though the row was invisible to its own creator.
--   7. documents.attachments (0018:32-38) — tenant-wide; a passport/proof-of-address is exactly
--      the kind of file book scoping exists for.
--   8. platform.instructions (0016:36-41) — tenant-wide money-movement intent.
--   9. tasks.tasks (0028:51-53) — read/USING were book-scoped but WITH CHECK stayed tenant-wide,
--      so a book-scoped staffer could CREATE a task assigned to a contact outside their book;
--      only application code prevented it. (Safe to tighten: _validate_assignee already runs
--      under the caller's RLS, so the app never legitimately writes an out-of-book assignee.)
--  10. crm.applications (0002:515-518) — scoped on `adviser_id = auth.uid()`, a fourth
--      inconsistent idiom that both over- and under-scopes: an adviser lost sight of their own
--      book's applications the moment another adviser was recorded on the row, and a client saw
--      none of their own. Brought onto contact_scope() via crm.account_contacts.
--
-- Scoping a link table (documents.shares) through the resource's OWN policy — `EXISTS (SELECT 1
-- FROM documents.documents d WHERE d.uid = ...)` — is deliberate: the subquery is itself
-- RLS-filtered, so "shares I can manage" is by construction "resources I can see", and it stays
-- correct automatically as the resource policies evolve.
--
-- WHICH SURFACES ACTUALLY CHANGE (read this before regression-testing the product). RLS only bites
-- where the request runs as `authenticated`, i.e. under db.py::tenant_session. Endpoints on
-- firm_session / admin_session / internal_session do `SET LOCAL ROLE service_role` (BYPASSRLS), so
-- these policies are invisible to them:
--
--   * flows.* — the journeys/cases surface (app/routers/applications.py, every endpoint) is on
--     firm_session, so an adviser sees NO behaviour change there. The flows policies below bite
--     app/routers/search.py (tenant_session) and any future authenticated reader; they are a
--     defence-in-depth floor under a service_role surface, not a product-visible fix today.
--   * platform.instructions — written/read via app/routers/firm.py on firm_session; same.
--   * crm.kyc_checks, documents.shares, documents.attachments, tasks.tasks, crm.applications —
--     these DO run under tenant_session (app/routers/{files,shares,tasks}.py, search.py), so the
--     tightening is observable through the API and is covered by the API suites too.
--
-- Expand-only: policies only, no DDL, no data change. lock_timeout per repo convention (0024).
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- ============================================================================
-- 1. crm.kyc_checks — contact_id is NOT NULL, so the (b)/(c) branches collapse into the exact
--    crm.contacts expression. Writes are service_role only (no authenticated write policy).
-- ============================================================================
DROP POLICY kyc_checks_read ON crm.kyc_checks;
CREATE POLICY kyc_checks_read ON crm.kyc_checks FOR SELECT TO authenticated USING (
  (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
  OR contact_id = ANY (auth.contact_scope())
);

-- ============================================================================
-- 2. flows.cases — subject_contact_id is nullable (a firm-internal / group case), which is the
--    "no client link" case of branch (b). Writes are service_role only (the engine).
-- ============================================================================
DROP POLICY cases_read ON flows.cases;
CREATE POLICY cases_read ON flows.cases FOR SELECT TO authenticated USING (
  (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
  OR (
    tenant_id = ANY (auth.tenant_lineage())
    AND (subject_contact_id IS NULL OR subject_contact_id = ANY (auth.contact_scope()))
  )
  OR subject_contact_id = ANY (auth.contact_scope())
);

-- ============================================================================
-- 3. flows.case_steps — scoped through its case. The EXISTS subquery is RLS-filtered by
--    cases_read above, so an out-of-book case is already invisible here; the explicit predicate
--    is kept so the intent survives a future change to cases_read.
-- ============================================================================
DROP POLICY case_steps_read ON flows.case_steps;
CREATE POLICY case_steps_read ON flows.case_steps FOR SELECT TO authenticated USING (
  (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
  OR (
    tenant_id = ANY (auth.tenant_lineage())
    AND EXISTS (
      SELECT 1 FROM flows.cases c
      WHERE c.id = flows.case_steps.case_id
        AND (c.subject_contact_id IS NULL OR c.subject_contact_id = ANY (auth.contact_scope()))
    )
  )
  OR EXISTS (
    SELECT 1 FROM flows.cases c
    WHERE c.id = flows.case_steps.case_id
      AND c.subject_contact_id = ANY (auth.contact_scope())
  )
);

-- ============================================================================
-- 4. flows.executions — the case group; its scope is the union of its cases' subjects.
--
--    This one CANNOT use the `NOT EXISTS (SELECT ... FROM flows.cases ...)` shape the documents
--    policies use. That shape is only sound because documents.document_contacts is tenant-wide
--    for staff, so the subquery sees every link; flows.cases is now itself book-scoped (above), so
--    an out-of-book case is invisible inside the subquery and NOT EXISTS would read as "no
--    out-of-book cases" — silently granting exactly what we are closing. So the subject set is
--    resolved by a SECURITY DEFINER accessor owned by service_role (the 0001 device), and the
--    policy is expressed positively: contained by my book (empty set = firm-internal / freshly
--    created, staff-visible) for staff, overlapping my scope for a client.
-- ============================================================================
--    CONTAINMENT: like the 0030 accessors, this is a BYPASSRLS definer function taking a
--    caller-supplied id, so without a guard `SELECT flows.execution_subject_contacts(<any id>)`
--    would hand any session — a client-portal session included — another firm's case subject
--    contact ids. It therefore resolves the execution's own tenant (unfiltered, as definer) and
--    returns NULL unless that tenant is the caller's own / in their lineage, or the caller is
--    Graphene ops. NULL rather than '{}' on purpose: '{}' <@ anything is TRUE, so a fail-closed
--    return must be the one value that makes both `<@` and `&&` below evaluate to NOT TRUE.
CREATE OR REPLACE FUNCTION flows.execution_subject_contacts(p_execution_id int) RETURNS int[]
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = 'flows', 'pg_catalog' AS $$
  SELECT CASE
    WHEN NOT EXISTS (
      SELECT 1 FROM flows.executions e
      WHERE e.id = p_execution_id
        AND (e.tenant_id = auth.tenant_id()
          OR e.tenant_id = ANY (auth.tenant_lineage())
          OR auth.is_system())
    ) THEN NULL
    ELSE COALESCE(
      (SELECT array_agg(DISTINCT c.subject_contact_id) FROM flows.cases c
        WHERE c.execution_id = p_execution_id AND c.subject_contact_id IS NOT NULL),
      '{}'::int[])
  END $$;

GRANT CREATE ON SCHEMA flows TO service_role;  -- the new owner needs CREATE on the schema
ALTER FUNCTION flows.execution_subject_contacts(int) OWNER TO service_role;
REVOKE ALL ON FUNCTION flows.execution_subject_contacts(int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION flows.execution_subject_contacts(int) TO authenticated, service_role;

DROP POLICY executions_read ON flows.executions;
CREATE POLICY executions_read ON flows.executions FOR SELECT TO authenticated USING (
  (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
  OR (
    tenant_id = ANY (auth.tenant_lineage())
    AND flows.execution_subject_contacts(id) <@ auth.contact_scope()
  )
  OR flows.execution_subject_contacts(id) && auth.contact_scope()
);

-- ============================================================================
-- 5. flows.domain_events — the append-only fact log; payloads carry step + client detail, so it
--    follows its case (or, for execution-level events, its execution). Both subqueries are
--    RLS-filtered by the policies above. service_role keeps INSERT (append-only).
-- ============================================================================
DROP POLICY domain_events_read ON flows.domain_events;
CREATE POLICY domain_events_read ON flows.domain_events FOR SELECT TO authenticated USING (
  (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
  OR (
    tenant_id = ANY (auth.tenant_lineage())
    AND (case_id IS NULL OR EXISTS (SELECT 1 FROM flows.cases c WHERE c.id = case_id))
    AND (execution_id IS NULL OR EXISTS (SELECT 1 FROM flows.executions e WHERE e.id = execution_id))
  )
  OR (case_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM flows.cases c
    WHERE c.id = case_id AND c.subject_contact_id = ANY (auth.contact_scope())
  ))
);

-- ============================================================================
-- 6. documents.shares — scoped through the shared resource's own policy, per resource_type. A
--    book-scoped adviser can therefore neither list, nor REVOKE, nor MINT a link to a document or
--    file they cannot see: the same predicate is the USING *and* the WITH CHECK.
--
--    Minting is the sharpest edge and the reason WITH CHECK cannot be left tenant-wide "because
--    app/routers/shares.py validates the document first". A share row is not just metadata — it
--    carries the bearer token, and GET /share/{token} resolves on service_role checking only token
--    + expiry, so an out-of-book INSERT is a working public download link for a document the
--    creator is not allowed to open. The row being invisible to its creator afterwards is no
--    mitigation; they already have the token.
--
--    resource_type: the live CHECK constraint is ('document', 'file', 'flow_case') — 0017 created it
--    with the first two and 0021_flows.sql:123-125 REPLACED it to add 'flow_case'. All three are
--    therefore reachable rows and all three get a branch. Nothing in app/ mints a 'file' or
--    'flow_case' share yet (only POST /documents/{uid}/share does), but "no caller today" is not a
--    reason to leave a branch out: a share whose resource_type this policy does not know reads as
--    "no visible resource" and becomes invisible to the firm that owns it. Fail-closed is the right
--    default for an unknown type, but it should not be the outcome for a type the schema permits.
--    If the CHECK is widened again, widen this policy in the SAME migration.
--
--    Each branch is a POSITIVE EXISTS through the resource's own RLS-filtered policy, which is the
--    shape that stays correct on its own as those policies evolve — including flows.cases, which
--    this very migration book-scopes. (Contrast flows.executions below, where the NEGATIVE shape
--    could not be used for exactly that reason.)
-- ============================================================================
DROP POLICY shares_read ON documents.shares;
DROP POLICY shares_write ON documents.shares;

CREATE POLICY shares_read ON documents.shares FOR SELECT TO authenticated USING (
  tenant_id = ANY (auth.tenant_lineage())
  AND (
    auth.can_read_all()
    OR (resource_type = 'document' AND EXISTS (
      SELECT 1 FROM documents.documents d
      WHERE d.uid = documents.shares.resource_uid AND d.tenant_id = documents.shares.tenant_id))
    OR (resource_type = 'file' AND EXISTS (
      SELECT 1 FROM documents.attachments a
      WHERE a.uid = documents.shares.resource_uid AND a.tenant_id = documents.shares.tenant_id))
    OR (resource_type = 'flow_case' AND EXISTS (
      SELECT 1 FROM flows.cases c
      WHERE c.uid = documents.shares.resource_uid AND c.tenant_id = documents.shares.tenant_id))
  )
);

CREATE POLICY shares_write ON documents.shares FOR ALL TO authenticated
  USING (
    tenant_id = ANY (auth.tenant_lineage())
    AND (
      auth.can_read_all()
      OR (resource_type = 'document' AND EXISTS (
        SELECT 1 FROM documents.documents d
        WHERE d.uid = documents.shares.resource_uid AND d.tenant_id = documents.shares.tenant_id))
      OR (resource_type = 'file' AND EXISTS (
        SELECT 1 FROM documents.attachments a
        WHERE a.uid = documents.shares.resource_uid AND a.tenant_id = documents.shares.tenant_id))
      OR (resource_type = 'flow_case' AND EXISTS (
        SELECT 1 FROM flows.cases c
        WHERE c.uid = documents.shares.resource_uid AND c.tenant_id = documents.shares.tenant_id))
    )
  )
  WITH CHECK (
    tenant_id = ANY (auth.tenant_lineage())
    AND (
      auth.can_read_all()
      OR (resource_type = 'document' AND EXISTS (
        SELECT 1 FROM documents.documents d
        WHERE d.uid = documents.shares.resource_uid AND d.tenant_id = documents.shares.tenant_id))
      OR (resource_type = 'file' AND EXISTS (
        SELECT 1 FROM documents.attachments a
        WHERE a.uid = documents.shares.resource_uid AND a.tenant_id = documents.shares.tenant_id))
      OR (resource_type = 'flow_case' AND EXISTS (
        SELECT 1 FROM flows.cases c
        WHERE c.uid = documents.shares.resource_uid AND c.tenant_id = documents.shares.tenant_id))
    )
  );

-- ============================================================================
-- 7. documents.attachments — contact_id is nullable (a firm-internal file). All three clauses
--    share the scoping expression. WITH CHECK is NOT left to files.py's _assert_visible_contact:
--    an attachment stamped with an out-of-book contact_id is a passport/proof-of-address filed
--    against a client the uploader may not see, and it is then visible to that client's own
--    adviser and to the client themselves. Uploads are staff-only (documents:write, "never the
--    client" — app/routers/files.py:150), so the lineage conjunct costs nothing.
-- ============================================================================
DROP POLICY attachments_read ON documents.attachments;
DROP POLICY attachments_write ON documents.attachments;

CREATE POLICY attachments_read ON documents.attachments FOR SELECT TO authenticated USING (
  (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
  OR (
    tenant_id = ANY (auth.tenant_lineage())
    AND (contact_id IS NULL OR contact_id = ANY (auth.contact_scope()))
  )
  OR contact_id = ANY (auth.contact_scope())
);

CREATE POLICY attachments_write ON documents.attachments FOR ALL TO authenticated
  USING (
    (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
    OR (
      tenant_id = ANY (auth.tenant_lineage())
      AND (contact_id IS NULL OR contact_id = ANY (auth.contact_scope()))
    )
    OR contact_id = ANY (auth.contact_scope())
  )
  WITH CHECK (
    tenant_id = ANY (auth.tenant_lineage())
    AND (
      auth.can_read_all()
      OR contact_id IS NULL
      OR contact_id = ANY (auth.contact_scope())
    )
  );

-- ============================================================================
-- 8. platform.instructions — carries both contact_id (nullable) and account_id (NOT NULL). A
--    book-scoped staffer's account_scope is derived from their book (auth.resolve_claims), so
--    either link places the row in their book. Writes stay staff-only, now book-scoped in USING.
-- ============================================================================
DROP POLICY instructions_read ON platform.instructions;
DROP POLICY instructions_write ON platform.instructions;

CREATE POLICY instructions_read ON platform.instructions FOR SELECT TO authenticated USING (
  (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
  OR contact_id = ANY (auth.contact_scope())
  OR account_id = ANY (auth.account_scope())
);

CREATE POLICY instructions_write ON platform.instructions FOR ALL TO authenticated
  USING (
    (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
    OR contact_id = ANY (auth.contact_scope())
    OR account_id = ANY (auth.account_scope())
  )
  -- Money-movement intent: raising one against a client outside your book is the whole point of
  -- the finding, so WITH CHECK repeats the scoping rather than trusting the router.
  WITH CHECK (
    tenant_id = ANY (auth.tenant_lineage())
    AND (
      auth.can_read_all()
      OR contact_id = ANY (auth.contact_scope())
      OR account_id = ANY (auth.account_scope())
    )
  );

-- ============================================================================
-- 9. tasks.tasks — read/USING keep the 0028 expression; WITH CHECK now matches it, so a
--    book-scoped staffer can no longer create or reassign a task onto a contact outside their
--    book. A client still writes only against their own action items (this is the one table the
--    client portal writes under `authenticated`, so the client branch stays) — but that branch now
--    carries `tenant_id = auth.tenant_id()`, because 0028's WITH CHECK let a client stamp ANOTHER
--    firm's tenant_id on a row as long as the assignee was their own contact.
-- ============================================================================
DROP POLICY tasks_write ON tasks.tasks;
CREATE POLICY tasks_write ON tasks.tasks FOR ALL TO authenticated
  USING (
    (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
    OR (
      tenant_id = ANY (auth.tenant_lineage())
      AND (assignee_contact_id IS NULL OR assignee_contact_id = ANY (auth.contact_scope()))
    )
    OR assignee_contact_id = ANY (auth.contact_scope())
  )
  WITH CHECK (
    (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
    OR (
      tenant_id = ANY (auth.tenant_lineage())
      AND (assignee_contact_id IS NULL OR assignee_contact_id = ANY (auth.contact_scope()))
    )
    OR (tenant_id = auth.tenant_id() AND assignee_contact_id = ANY (auth.contact_scope()))
  );

-- ============================================================================
-- 10. crm.applications — off `adviser_id = auth.uid()` and onto contact_scope(), via the
--     application↔contact join (crm.account_contacts). adviser_id is KEPT as an additional
--     branch so an adviser never loses their own applications, but it is no longer the scope.
--     Reads only; writes are service_role.
-- ============================================================================
DROP POLICY applications_read ON crm.applications;
CREATE POLICY applications_read ON crm.applications FOR SELECT TO authenticated USING (
  (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
  OR (tenant_id = ANY (auth.tenant_lineage()) AND adviser_id = auth.uid())
  OR EXISTS (
    SELECT 1 FROM crm.account_contacts ac
    WHERE ac.application_id = crm.applications.id
      AND ac.contact_id = ANY (auth.contact_scope())
  )
);

-- ============================================================================
-- A LOAD-BEARING INVARIANT, recorded where the next person will trip over it.
--
-- documents_read (0021_documents_scope) and documents_audit_read (0022) book-scope with a NEGATIVE
-- subquery: `NOT EXISTS (SELECT 1 FROM documents.document_contacts dc WHERE ... AND dc.contact_id
-- <> ALL (auth.contact_scope()))` — "no link to a contact outside my book". A policy predicate's
-- own subqueries ARE RLS-filtered, so that shape is sound ONLY while document_contacts stays
-- tenant-wide for staff. Book-scope it and the out-of-book links go invisible inside the subquery,
-- NOT EXISTS reads as "clean" for every row, and branch (b) collapses to plain
-- `tenant_id = ANY (auth.tenant_lineage())` — tenant-wide, silently re-opening exactly what
-- 0021/0022 closed.
--
-- Verified, not theorised: book-scoping BOTH of document_contacts' policies in a scratch build made
-- an out-of-book document (linked only to another adviser's client) readable by a book-scoped
-- adviser. Two details that experiment turned up, both worth knowing before anyone tries it again:
--
--   * It takes both policies. document_contacts_write is FOR ALL, and Postgres ORs every applicable
--     policy's USING together for SELECT — so tightening only document_contacts_read changes
--     nothing at all. (Same trap as item 6-8 above, from the other side.)
--   * A document co-delivered to an in-book AND an out-of-book contact is visible to the book-scoped
--     adviser TODAY, legitimately: documents_read's third branch is positive (`EXISTS (a link in my
--     scope)`) and is not client-only. So a two-books fixture is NOT a valid tripwire for this
--     hazard — it passes either way. The tripwire has to use a document linked ONLY out of book;
--     that is tests/test_rls_book_scope.py::test_documents_stay_book_scoped_via_the_not_exists_shape.
--
-- flows.executions above had to abandon the NOT EXISTS shape for a SECURITY DEFINER accessor for
-- precisely this reason, flows.cases now being book-scoped. Anyone tightening document_contacts must
-- convert 0021/0022 to the positive/definer shape in the same migration.
-- ============================================================================
COMMENT ON POLICY document_contacts_read ON documents.document_contacts IS
  'DELIBERATELY TENANT-WIDE FOR STAFF, and load-bearing: documents_read (0021) / '
  'documents_audit_read (0022) book-scope via NOT EXISTS over this table, and policy subqueries are '
  'RLS-filtered. Book-scoping this policy (and document_contacts_write, whose FOR ALL USING is ORed '
  'in for SELECT) collapses their staff branch to tenant-wide and silently re-opens cross-book '
  'document reads. If you tighten it, rewrite 0021/0022 onto the positive (or SECURITY DEFINER) '
  'shape in the same migration -- see 0031 for the pattern and the tripwire test.';
