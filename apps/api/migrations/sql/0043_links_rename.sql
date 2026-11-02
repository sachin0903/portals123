-- ============================================================================
-- 0043_links_rename -- platform.platform_links becomes platform.links.
--
-- The table was named when it lived beside a dozen `platform_*` things; inside the `platform`
-- schema the prefix only stutters (`platform.platform_links`). Everything that reads it is
-- already saying "the links table".
--
-- A rename, not a copy: Postgres carries the indexes, constraints, policies, triggers, grants
-- and the audit attachment across `ALTER TABLE ... RENAME`, so the only things needing explicit
-- work are the objects that reference the table BY NAME in their own definition -- the
-- entity_links view (0042) -- and the constraint/index names, which are cosmetic but would
-- otherwise keep the old word alive in every \d output.
-- ============================================================================

-- The view names the table in its body, so it has to go first and come back after.
DROP VIEW IF EXISTS platform.entity_links;

ALTER TABLE platform.platform_links RENAME TO links;

-- Constraint and index names follow the table. Renaming these is not required for
-- correctness -- it is required for anyone reading the schema to find them.
ALTER TABLE platform.links RENAME CONSTRAINT platform_links_unique_link TO links_unique_link;
ALTER INDEX platform.platform_links_entity_idx RENAME TO links_entity_idx;
ALTER INDEX platform.platform_links_provider_external_idx RENAME TO links_provider_external_idx;
ALTER INDEX platform.platform_links_tenant_id_idx RENAME TO links_tenant_id_idx;

-- The read policy keeps its behaviour; only its name mentioned the old table.
ALTER POLICY platform_links_read ON platform.links RENAME TO links_read;

-- Recreated against the new name (0042's comment explains why transactions and valuations
-- carry their provider key in-row rather than as link rows) -- and with `security_invoker`,
-- which 0042 missed.
--
-- Without it a view runs with the permissions of its OWNER: the migrator, which is subject to
-- FORCE ROW LEVEL SECURITY and carries no request claims, so every policy evaluated against an
-- empty `auth.tenant_lineage()` and the view returned ZERO rows to everyone -- including
-- service_role, whose BYPASSRLS does not apply because it was not the role being checked. With
-- it, the caller's own policies apply, which is what a view over RLS'd tables has to do.
CREATE VIEW platform.entity_links WITH (security_invoker = true) AS
  SELECT tenant_id, entity_type, entity_id, provider, external_id, link_type, updated_at
  FROM platform.links
  UNION ALL
  SELECT tenant_id, 'transaction', id, source_system, source_transaction_id, 'primary', updated_at
  FROM platform.transactions
  UNION ALL
  SELECT tenant_id, 'valuation', id, source_system,
         account_id || ':' || to_char(as_of_date, 'YYYY-MM-DD'), 'primary', updated_at
  FROM platform.valuations;

GRANT SELECT ON platform.entity_links TO authenticated, service_role;
