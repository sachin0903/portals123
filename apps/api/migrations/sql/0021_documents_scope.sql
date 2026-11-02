-- 0021_documents_scope: two related document-visibility fixes.
--
-- 1. Adviser book scoping. documents_read's staff branch was just "any staff in this tenant" --
--    unlike crm.contacts, which already scopes a staffer to their assigned book (auth.contact_scope())
--    unless they hold contacts:read_all. A book-scoped adviser could see and send every client's
--    documents, not just their own. This mirrors the crm.contacts pattern: full visibility with
--    contacts:read_all; otherwise only documents with no client link at all (unassigned, e.g.
--    firm-internal), or linked only to contacts within the caller's own book.
--
-- 2. Client over-visibility. generate_document bound the target client's contact into
--    documents.document_contacts -- the exact table documents_read trusts as "this was delivered
--    to this client" -- immediately at generation time, before the document even rendered and
--    before any explicit Send. A client could see their own queued/generating/failed documents
--    without an adviser ever sending anything. `intended_contact_id` replaces that: plain
--    bookkeeping (who a generated document is FOR, shown in the adviser's list), read by no RLS
--    policy. A client only becomes visible in the client portal once POST /documents/{uid}/send
--    actually delivers it via documents.document_contacts.

ALTER TABLE documents.documents
    ADD COLUMN intended_contact_id int REFERENCES crm.contacts(id) ON DELETE SET NULL;

-- documents_write (0005) is FOR ALL with a tenant-wide USING clause -- for SELECT, Postgres ORs
-- every applicable policy's USING clause together, so that permissive clause alone let any tenant
-- staff read/write any document regardless of book scoping, no matter how documents_read was
-- tightened. Both policies now share the same scoping expression for existing-row visibility;
-- WITH CHECK stays tenant-wide (any staff may create a document for their tenant -- per-contact
-- targeting is validated at the application layer, see _assert_visible_contact in documents.py).
DROP POLICY documents_read ON documents.documents;
DROP POLICY documents_write ON documents.documents;

CREATE POLICY documents_read ON documents.documents FOR SELECT TO authenticated USING (
    -- Full-book staff (contacts:read_all): every document in the tenant.
    (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
    -- Book-scoped staff: documents with no client link at all, or linked only to contacts within
    -- the caller's own book.
    OR (
        tenant_id = ANY (auth.tenant_lineage())
        AND NOT EXISTS (
            SELECT 1 FROM documents.document_contacts dc
            WHERE dc.document_id = documents.documents.id
              AND dc.contact_id <> ALL (auth.contact_scope())
        )
    )
    -- Clients: documents explicitly delivered to one of their own contacts.
    OR EXISTS (
        SELECT 1 FROM documents.document_contacts dc
        WHERE dc.document_id = documents.documents.id
          AND dc.contact_id = ANY (auth.contact_scope())
    )
);

CREATE POLICY documents_write ON documents.documents FOR ALL TO authenticated
    USING (
        (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
        OR (
            tenant_id = ANY (auth.tenant_lineage())
            AND NOT EXISTS (
                SELECT 1 FROM documents.document_contacts dc
                WHERE dc.document_id = documents.documents.id
                  AND dc.contact_id <> ALL (auth.contact_scope())
            )
        )
        OR EXISTS (
            SELECT 1 FROM documents.document_contacts dc
            WHERE dc.document_id = documents.documents.id
              AND dc.contact_id = ANY (auth.contact_scope())
        )
    )
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
