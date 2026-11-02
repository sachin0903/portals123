-- Tighten the documents.audit_log INSERT policy.
--
-- 0005 shipped `documents_audit_append` as `WITH CHECK (true)`, which let ANY authenticated
-- principal append audit rows with an ARBITRARY tenant_id -- i.e. forge or pollute another
-- tenant's document activity / consent trail (a compliance-record integrity hole; every sibling
-- policy scopes writes by tenant).
--
-- Scope the write to the caller's OWN tenant. Subtlety: a 'client' principal has an EMPTY
-- auth.tenant_lineage() (the 0001 safety lever), yet clients DO append events (document.viewed)
-- for their own documents. So this must key on auth.tenant_id() (the caller's tenant, which
-- clients have), NOT auth.tenant_lineage() (empty for clients -> would reject their inserts and
-- break document viewing). Every legitimate insert flows through app/routers/documents.py::_audit,
-- which sets `tenant_id = auth.tenant_id()`, so this check always passes for real writes and only
-- blocks a hand-crafted cross-tenant tenant_id. `auth.is_system()` keeps the Graphene ops bypass.
DROP POLICY IF EXISTS documents_audit_append ON documents.audit_log;
CREATE POLICY documents_audit_append ON documents.audit_log FOR INSERT TO authenticated
    WITH CHECK (auth.is_system() OR tenant_id = auth.tenant_id());
