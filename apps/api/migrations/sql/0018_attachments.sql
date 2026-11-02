-- 0018_attachments: files as a first-class entity, distinct from documents.
--
-- A "document" (documents.documents) is a financial document — generated or uploaded (suitability
-- report, illustration, statement). A "file" (documents.attachments) is a supporting upload about a
-- client — a passport, ID, proof of address. Files are NOT a document type: separate table, own
-- router, own lifecycle (upload-only; never generated). Both reference the same blob layer
-- (documents.files) and bind to the true client model (crm.contacts), so a client sees their own
-- files through auth.contact_scope() exactly as they see their own documents.

CREATE TABLE documents.attachments (
    id             uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    uid            text        NOT NULL UNIQUE,          -- public id; API paths use this (att_...)
    tenant_id      int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    file_id        uuid        NOT NULL REFERENCES documents.files(id) ON DELETE RESTRICT,
    contact_id     int         REFERENCES crm.contacts(id) ON DELETE SET NULL,  -- the client it's about
    attachment_type text       NOT NULL DEFAULT 'other',  -- passport / national_id / proof_of_address / other
    name           text,
    uploaded_by    uuid        REFERENCES auth.users(id),
    created_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz
);
CREATE INDEX attachments_tenant_id_idx  ON documents.attachments(tenant_id);
CREATE INDEX attachments_contact_id_idx ON documents.attachments(contact_id);
CREATE INDEX attachments_type_idx       ON documents.attachments(tenant_id, attachment_type);

-- ===== RLS + grants =========================================================================
-- Staff by tenant lineage; the linked client reads their own via contact_scope (mirrors the
-- documents.documents client branch, keyed on the local contact_id column — no subquery recursion).
ALTER TABLE documents.attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.attachments FORCE ROW LEVEL SECURITY;

CREATE POLICY attachments_read ON documents.attachments FOR SELECT TO authenticated USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR contact_id = ANY (auth.contact_scope())
);
CREATE POLICY attachments_write ON documents.attachments FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));

GRANT SELECT, INSERT, UPDATE, DELETE ON documents.attachments TO authenticated;
GRANT ALL ON documents.attachments TO service_role;
