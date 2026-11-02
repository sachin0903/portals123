-- 0022_documents_activity_scope: extend 0021's adviser-book scoping to the activity trail and to
-- requests, and fix client visibility of their own audit events.
--
-- 0021 book-scoped documents.documents but left three sibling policies tenant-wide:
--
-- 1. documents.audit_log (documents_audit_read, from 0005) was `tenant_id = ANY(tenant_lineage())`.
--    A book-scoped adviser (no contacts:read_all) therefore read EVERY tenant audit row through the
--    overview activity feed -- including sends to other advisers' clients, whose names ride in the
--    event `detail` -- and a CLIENT (empty tenant_lineage) read ZERO rows, so their own
--    `document.viewed` events were invisible to them: the Unread/Read badge and Last-interacted
--    sort (both driven by reading the caller's own view events back in documents.py::_SELECT) were
--    permanently stuck. The rewrite mirrors documents_read (0021): full trail with
--    contacts:read_all; book-scoped staff see audit for documents with no out-of-book contact link;
--    clients see audit for documents delivered to one of their own contacts (which also un-sticks
--    Unread). Template-level rows (document_id IS NULL) carry no document_contacts, so the
--    book-scoped branch keeps them staff-visible and never exposes them to clients.
--
-- 2. documents.requests / request_items (requests_read/request_items_read, from 0015) were
--    `tenant_lineage() OR contact_scope`, with no contacts:read_all gate -- so a book-scoped adviser
--    saw the whole firm's requests. Gate the tenant-wide branch behind can_read_all(), keeping the
--    per-contact (book / client) branch.

DROP POLICY documents_audit_read ON documents.audit_log;
CREATE POLICY documents_audit_read ON documents.audit_log FOR SELECT TO authenticated USING (
    -- Full-book staff (contacts:read_all): every audit row in the tenant.
    (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
    -- Book-scoped staff: audit for documents with no client link at all, or linked only to
    -- contacts within the caller's own book.
    OR (
        tenant_id = ANY (auth.tenant_lineage())
        AND NOT EXISTS (
            SELECT 1 FROM documents.document_contacts dc
            WHERE dc.document_id = documents.audit_log.document_id
              AND dc.contact_id <> ALL (auth.contact_scope())
        )
    )
    -- Clients: audit for documents delivered to one of their own contacts.
    OR EXISTS (
        SELECT 1 FROM documents.document_contacts dc
        WHERE dc.document_id = documents.audit_log.document_id
          AND dc.contact_id = ANY (auth.contact_scope())
    )
);

DROP POLICY requests_read ON documents.requests;
CREATE POLICY requests_read ON documents.requests FOR SELECT TO authenticated USING (
    (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
    OR contact_id = ANY (auth.contact_scope())
);

DROP POLICY request_items_read ON documents.request_items;
CREATE POLICY request_items_read ON documents.request_items FOR SELECT TO authenticated USING (
    (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
    OR EXISTS (
        SELECT 1 FROM documents.requests r
        WHERE r.id = request_id AND r.contact_id = ANY (auth.contact_scope())
    )
);

-- The FOR ALL write policies (0008) still USING (tenant_id = ANY(tenant_lineage())) -- and for
-- SELECT Postgres ORs every applicable policy's USING together, so that tenant-wide clause alone
-- re-exposed every request to a book-scoped adviser no matter how requests_read was tightened
-- (the same trap 0021 fixed for documents_write). Match the read scoping in USING; WITH CHECK
-- stays tenant-wide (staff raise requests for their tenant; clients fulfil via the service_role
-- endpoint, never as `authenticated`, so their empty lineage still correctly blocks self-issue).
DROP POLICY requests_write ON documents.requests;
CREATE POLICY requests_write ON documents.requests FOR ALL TO authenticated
    USING (
        (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
        OR contact_id = ANY (auth.contact_scope())
    )
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));

DROP POLICY request_items_write ON documents.request_items;
CREATE POLICY request_items_write ON documents.request_items FOR ALL TO authenticated
    USING (
        (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
        OR EXISTS (
            SELECT 1 FROM documents.requests r
            WHERE r.id = request_id AND r.contact_id = ANY (auth.contact_scope())
        )
    )
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
