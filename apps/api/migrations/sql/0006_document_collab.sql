-- 0006_document_collab: adviser<->client collaboration on documents (client links).
--
-- TEMPORARY binding: document_contacts.user_id references auth.users (a client IS a portal user)
-- rather than crm.contacts, because platform_crm/crm.contacts isn't built yet. This unlocks the
-- client document view now, with client-visibility RLS keyed on auth.uid(). When crm.contacts lands,
-- add a contact_id and reconcile these links (a follow-up migration) — the binding here is
-- intentionally interim. See docs/document-management.md.

-- Which client users a document is shared with — drives client visibility. tenant_id is
-- denormalised (from the parent doc) so this table's RLS can scope by tenant WITHOUT a subquery
-- back into documents — a documents<->document_contacts subquery cycle causes RLS infinite recursion.
CREATE TABLE documents.document_contacts (
    document_id uuid        NOT NULL REFERENCES documents.documents(id) ON DELETE CASCADE,
    tenant_id   int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    user_id     uuid        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,  -- TEMP: -> crm.contacts later
    role        text        NOT NULL DEFAULT 'recipient',
    added_by    uuid        REFERENCES auth.users(id),
    added_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, user_id, role)
);
CREATE INDEX document_contacts_user_id_idx ON documents.document_contacts(user_id);

-- ===== RLS + grants =========================================================================

-- document_contacts: staff by tenant; the linked client reads their own. Scoped by the local
-- tenant_id column (no subquery into documents) to avoid RLS recursion with documents_read.
ALTER TABLE documents.document_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.document_contacts FORCE ROW LEVEL SECURITY;
CREATE POLICY document_contacts_read ON documents.document_contacts FOR SELECT TO authenticated USING (
    user_id = auth.uid() OR tenant_id = ANY (auth.tenant_lineage())
);
CREATE POLICY document_contacts_write ON documents.document_contacts FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents.document_contacts TO authenticated;
GRANT ALL ON documents.document_contacts TO service_role;

-- Upgrade documents + files reads with the CLIENT branch: a linked client sees the doc (and its
-- blobs). Writes stay staff-only (clients act via requests, not by editing the document row).
DROP POLICY documents_read ON documents.documents;
CREATE POLICY documents_read ON documents.documents FOR SELECT TO authenticated USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR EXISTS (
        SELECT 1 FROM documents.document_contacts dc
        WHERE dc.document_id = documents.documents.id AND dc.user_id = auth.uid()
    )
);

DROP POLICY documents_files_read ON documents.files;
CREATE POLICY documents_files_read ON documents.files FOR SELECT TO authenticated USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR EXISTS (
        SELECT 1 FROM documents.documents d
        WHERE documents.files.id IN (d.original_file_id, d.pdf_file_id, d.html_file_id)
    )
);

GRANT USAGE ON ALL SEQUENCES IN SCHEMA documents TO authenticated, service_role;
