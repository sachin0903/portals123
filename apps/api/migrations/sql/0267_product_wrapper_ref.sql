-- ============================================================================
-- 0267_product_wrapper_ref — a platform's reference for a wrapper is the wrapper
-- itself, so fill it in rather than show a bare "—".
--
-- product_platforms.external_id is the custodian's / administrator's own
-- reference for the product, shown on the catalogue shelf. Enabling SEI or
-- Morgan Lloyd attached the row with a role but no external id, and the nightly
-- merge only stamps one from an account that already exists at the provider. A
-- wrapper the firm offers but has no account for yet therefore showed its
-- enabled custodian with no reference at all — "SEI custodian —".
--
-- The reference does not vary between firms: SEI custodies a GIA as a GIA
-- whether or not an account exists. Fill every null external id from the
-- product's own wrapper type. A value the merge or an operator already set is
-- left alone; `attach_custodians` / `attach_scheme_administrators` stamp the
-- same default for rows created after this.
--
-- As service_role: platform.product_platforms is FORCE RLS and the migrator is
-- not BYPASSRLS. An UPDATE without the role reports success and changes nothing.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE service_role;

UPDATE platform.product_platforms pp
   SET external_id = p.product_type::text,
       updated_at = now()
  FROM platform.products p
 WHERE pp.product_id = p.id
   AND pp.external_id IS NULL;

RESET ROLE;
