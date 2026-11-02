-- ============================================================================
-- 0082_storage_tenants_client_read -- a client can read their OWN tenant's
-- storage row.
--
-- storage.tenants' read policy was `tenant_id = ANY(auth.tenant_lineage())`
-- only. tenant_lineage() is DELIBERATELY empty for a client-portal principal
-- (the same mechanism that keeps a client off tenant-wide data elsewhere,
-- e.g. docs/aml.md's tipping-off control) -- so a client's own session could
-- never resolve their own tenant's bucket, even though the row exists and is
-- active. That's a real gap, not the tipping-off pattern working as intended:
-- bucket metadata (bucket name/endpoint/status) isn't sensitive the way AML
-- data is, and a client legitimately needs to know their own tenant's bucket
-- exists to upload their own chat attachment (app/messaging.py).
--
-- auth.tenant_id() is the right signal to add: it reads the JWT's tenant_id
-- claim directly (falling back to a WorkOS-org lookup), the raw "which
-- tenant does this login belong to" fact -- populated for clients AND staff,
-- unrelated to tenant_lineage()'s staff-book-reach semantics. This widens
-- the policy to "my own tenant, by either signal", never another tenant's.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

DROP POLICY storage_tenants_read ON storage.tenants;
CREATE POLICY storage_tenants_read ON storage.tenants FOR SELECT TO authenticated USING (
  tenant_id = ANY (auth.tenant_lineage())
  OR tenant_id = auth.tenant_id()
);
