-- 0015_document_shares: time-limited public share links for documents (and, later, files).
--
-- A share is a bearer token that grants unauthenticated, expiry-bounded read access to one
-- resource's blob. The public resolve path (GET /share/{token}) runs as service_role (BYPASSRLS),
-- looks the row up by token, checks it's live, and hands back a short-lived presigned URL — it
-- never trusts a caller identity, only the token + expiry.
--
-- Polymorphic on purpose: resource_type is 'document' today; 'file' lands with the files entity
-- (documents.attachments), so one share mechanism covers both. Staff-only RLS (tenant lineage) for
-- management; access is NOT recorded via the DML audit spine (that would log the bearer token) —
-- product events go to documents.audit_log (share.created / share.accessed / share.revoked).

CREATE TABLE documents.shares (
    id               uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    uid              text        NOT NULL UNIQUE,   -- public management id (shr_...); NOT the secret
    tenant_id        int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    resource_type    text        NOT NULL CHECK (resource_type IN ('document', 'file')),
    resource_uid     text        NOT NULL,          -- documents.documents.uid (or attachments.uid)
    token            text        NOT NULL UNIQUE,   -- the bearer secret in the public link
    expires_at       timestamptz NOT NULL,
    revoked_at       timestamptz,                   -- NULL => still active
    revoked_by       uuid        REFERENCES auth.users(id) ON DELETE SET NULL,
    access_count     int         NOT NULL DEFAULT 0,
    last_accessed_at timestamptz,
    created_by       uuid        REFERENCES auth.users(id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX shares_tenant_id_idx ON documents.shares(tenant_id);
CREATE INDEX shares_resource_idx  ON documents.shares(resource_type, resource_uid);
-- token already has a UNIQUE index; the public lookup filters on it.

-- ===== RLS + grants =========================================================================
-- Staff-only (tenant lineage). The public resolve endpoint uses service_role, which bypasses RLS.
ALTER TABLE documents.shares ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.shares FORCE ROW LEVEL SECURITY;

CREATE POLICY shares_read ON documents.shares FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()));
CREATE POLICY shares_write ON documents.shares FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));

GRANT SELECT, INSERT, UPDATE, DELETE ON documents.shares TO authenticated;
GRANT ALL ON documents.shares TO service_role;
