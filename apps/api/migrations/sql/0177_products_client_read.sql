-- ============================================================================
-- 0177_products_client_read -- a client can read their own firm's product shelf.
--
-- products_read was `tenant_id IS NULL OR tenant_id = ANY(auth.tenant_lineage())`.
-- tenant_lineage() is empty in the client portal (0004), so a LEFT JOIN from
-- platform.accounts onto platform.products dropped every firm-scoped row and
-- /crm/home fell back to type = 'Account' on every wrapper card. The names
-- were fine (they live on the account); the badges were not.
--
-- Same lever as 0082: auth.tenant_id() is the login's own firm, populated for
-- clients too. The shelf is not sensitive the way AML is — a client already
-- holds accounts of those types. Global (tenant_id IS NULL) catalogue rows
-- stay visible to everyone.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

DROP POLICY IF EXISTS products_read ON platform.products;
CREATE POLICY products_read ON platform.products FOR SELECT TO authenticated
  USING (
    tenant_id IS NULL
    OR tenant_id = ANY (auth.tenant_lineage())
    OR tenant_id = auth.tenant_id()
  );
