-- 0005_documents_storage: private document storage base.
--
-- The upload/generate STORAGE layer, buildable ahead of the platform_crm migration. Only the
-- crm/platform-independent pieces live here: documents.files, documents.documents, and the
-- product audit_log, with STAFF-ONLY RLS. The crm/platform-linked pieces land with platform_crm:
--   * FKs for account_id -> platform.accounts, application_id -> crm.applications;
--   * document_contacts / document_tasks / document_tags / upload_requests (contact_id -> crm);
--   * client-visible RLS (the contact_scope branch on documents/files);
--   * sharing via crm.shares (+ resource_uid) and the documents.resolve_share function.
-- Forward-ref columns (template_version_id/run_id/account_id/application_id) are plain ints here;
-- their FK constraints are ADDed (expand-only) by those later migrations. See docs/document-management.md.

CREATE SCHEMA IF NOT EXISTS documents;
GRANT USAGE ON SCHEMA documents TO anon, authenticated, service_role;

-- Canonical status enum (no 'pending_action': "action needed" is derived from open tasks later).
CREATE TYPE documents.document_status AS ENUM (
    'queued', 'generating', 'generated', 'failed', 'superseded', 'archived'
);

-- files -- blob metadata. Path-addressed, immutable once written. sha256 is integrity-only.
CREATE TABLE documents.files (
    id           uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    bucket       text        NOT NULL,
    storage_key  text        NOT NULL,   -- uploads/{tenantId}/{docUid}/original.{ext}
                                         -- documents/{tenantId}/{docUid}/v{n}.{pdf|html}
    content_type text        NOT NULL,
    size_bytes   bigint      NOT NULL,
    sha256       text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT files_bucket_key_key UNIQUE (bucket, storage_key)
);
CREATE INDEX files_tenant_id_idx ON documents.files(tenant_id);
CREATE INDEX files_sha256_idx ON documents.files(sha256);

-- documents -- the record for uploaded + generated docs. Generation-only columns (inputs,
-- dataset_snapshots, calculation_outputs, calc_lib_version, renderer_version, error, generated_at)
-- are written only by the render worker (Phase 2). Forward-ref columns get their FKs later.
CREATE TABLE documents.documents (
    id                   uuid                      PRIMARY KEY DEFAULT uuid_generate_v4(),
    uid                  text                      NOT NULL UNIQUE,   -- public id; API paths use this
    tenant_id            int                       NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    template_version_id  int,                      -- FK added by the generation migration
    run_id               int,                      -- FK added by the generation migration
    account_id           int,                      -- FK -> platform.accounts added by platform_crm
    application_id       int,                      -- FK -> crm.applications added by platform_crm
    original_file_id     uuid                      REFERENCES documents.files(id) ON DELETE SET NULL,  -- uploaded original (any allowed type)
    pdf_file_id          uuid                      REFERENCES documents.files(id) ON DELETE SET NULL,  -- the PDF rendition (== original for an uploaded PDF)
    html_file_id         uuid                      REFERENCES documents.files(id) ON DELETE SET NULL,
    superseded_by        uuid                      REFERENCES documents.documents(id) ON DELETE SET NULL,
    generated_by         uuid                      REFERENCES auth.users(id),
    document_type        text                      NOT NULL,
    source               text                      NOT NULL DEFAULT 'generated' CHECK (source IN ('generated', 'uploaded')),
    name                 text,
    status               documents.document_status NOT NULL DEFAULT 'queued',
    inputs               jsonb                     NOT NULL DEFAULT '{}',
    dataset_snapshots    jsonb                     NOT NULL DEFAULT '{}',
    calculation_outputs  jsonb                     NOT NULL DEFAULT '{}',
    metadata             jsonb                     NOT NULL DEFAULT '{}',
    calc_lib_version     text,
    renderer_version     text,
    error                text,
    generated_at         timestamptz,
    created_at           timestamptz               NOT NULL DEFAULT now()
);
CREATE INDEX documents_tenant_id_idx    ON documents.documents(tenant_id);
CREATE INDEX documents_type_created_idx ON documents.documents(document_type, created_at DESC);
CREATE INDEX documents_status_idx       ON documents.documents(status);
CREATE INDEX documents_inputs_gin       ON documents.documents USING gin (inputs);

-- audit_log -- product-semantic actions (uploaded, pdf_viewed, downloaded...). Append-only;
-- disjoint from the 0001 audit.log DML spine. NB: audit.record() casts row id to bigint, so uuid
-- document rows land in audit.log with entity_id = NULL (full row still in detail).
CREATE TABLE documents.audit_log (
    id          bigint      PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    tenant_id   int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    document_id uuid        REFERENCES documents.documents(id) ON DELETE SET NULL,
    template_id int,        -- FK added by the generation migration
    actor_id    uuid        REFERENCES auth.users(id),
    action      text        NOT NULL,
    detail      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX documents_audit_log_tenant_id_idx ON documents.audit_log(tenant_id);
CREATE INDEX documents_audit_log_document_idx  ON documents.audit_log(document_id, created_at DESC);

-- ===== RLS + grants =========================================================================
-- STAFF-ONLY for now (tenant lineage). The client-visible branch (contact_scope via
-- document_contacts) is added by platform_crm, which also creates document_contacts.

ALTER TABLE documents.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.documents FORCE ROW LEVEL SECURITY;
CREATE POLICY documents_read ON documents.documents FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()));
CREATE POLICY documents_write ON documents.documents FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents.documents TO authenticated;
GRANT ALL ON documents.documents TO service_role;

ALTER TABLE documents.files ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.files FORCE ROW LEVEL SECURITY;
CREATE POLICY documents_files_read ON documents.files FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()));
CREATE POLICY documents_files_write ON documents.files FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents.files TO authenticated;
GRANT ALL ON documents.files TO service_role;

ALTER TABLE documents.audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY documents_audit_read ON documents.audit_log FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()));
CREATE POLICY documents_audit_append ON documents.audit_log FOR INSERT TO authenticated
    WITH CHECK (true);
GRANT SELECT, INSERT ON documents.audit_log TO authenticated;  -- append-only
GRANT ALL ON documents.audit_log TO service_role;

GRANT USAGE ON ALL SEQUENCES IN SCHEMA documents TO authenticated, service_role;

-- Free row-level DML trail (from 0001) on the documents record.
SELECT audit.attach(ARRAY['documents.documents']);
