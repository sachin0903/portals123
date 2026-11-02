-- ============================================================================
-- 0044_products_multi_platform -- a product is one shelf item across every platform.
--
-- 0002 gave platform.products a NOT NULL platform_id, which made a product a property of ONE
-- platform: a firm offering a GIA at Seccl and a GIA at SEI needed two product rows, and the
-- two would drift in name, fee package and enablement while describing the same thing the
-- firm sells. 0042 then added platform.product_platforms -- external_id/external_name per
-- platform -- which is the right place for a platform's own reference, and left products
-- itself still bound to one.
--
-- So: platform_id becomes nullable, and (tenant_id, product_type) becomes the firm's key.
-- A product is what the firm offers; product_platforms says how each custodian names it.
--
-- The materialiser depends on this: it discovers the shelf from the wrappers a firm holds,
-- and while products were per-platform, SEI discovering GIA would have created a SECOND GIA
-- beside Seccl's rather than adding a reference to the existing one.
-- ============================================================================

-- Nullable, not dropped. Legacy global rows (tenant_id IS NULL, pre-0042) still point at the
-- platform they were created for, and nothing is gained by erasing that.
ALTER TABLE platform.products ALTER COLUMN platform_id DROP NOT NULL;

-- One row per wrapper per firm. Partial, because the legacy global rows are not tenant-scoped
-- and several of them legitimately share a product_type across different platforms.
--
-- DML in a migration runs as the migrator, which owns these tables but is NOT bypassrls and IS
-- subject to FORCE ROW LEVEL SECURITY -- so a bare SELECT here returns zero rows and the
-- de-duplication below would silently find nothing to do. SET LOCAL ROLE service_role is what
-- makes it see the data (precedent: 0023_flows_hardening, and 0041's backfill).
SET LOCAL ROLE service_role;

-- Fold any pre-existing per-platform duplicates into the lowest id, moving their platform
-- references across first so a reference is never lost with the row that held it.
WITH ranked AS (
  SELECT id, tenant_id, product_type,
         min(id) OVER (PARTITION BY tenant_id, product_type) AS keep_id
  FROM platform.products
  WHERE tenant_id IS NOT NULL
)
UPDATE platform.product_platforms pp
   SET product_id = r.keep_id
  FROM ranked r
 WHERE pp.product_id = r.id AND r.id <> r.keep_id
   -- ON CONFLICT is not available to an UPDATE, so skip a move that would collide with a
   -- reference the surviving product already has for that platform. The duplicate's row is
   -- then removed with its product below.
   AND NOT EXISTS (
     SELECT 1 FROM platform.product_platforms keep
      WHERE keep.product_id = r.keep_id AND keep.platform_id = pp.platform_id
   );

DELETE FROM platform.products p
 WHERE p.tenant_id IS NOT NULL
   AND p.id <> (SELECT min(q.id) FROM platform.products q
                 WHERE q.tenant_id = p.tenant_id AND q.product_type = p.product_type);

RESET ROLE;

-- Not CONCURRENTLY: this table is small (a firm's shelf is single digits) and a migration
-- transaction cannot build an index concurrently anyway.
CREATE UNIQUE INDEX products_tenant_type_key
    ON platform.products (tenant_id, product_type)
 WHERE tenant_id IS NOT NULL;

COMMENT ON COLUMN platform.products.platform_id IS
  'Legacy: the platform a pre-0044 product was created for. A tenant-scoped product spans '
  'platforms -- see platform.product_platforms for each platform''s own reference.';
