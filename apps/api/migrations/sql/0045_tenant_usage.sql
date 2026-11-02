-- ============================================================================
-- 0045_tenant_usage -- cached object-storage totals per tenant.
--
-- WHY A CACHE AND NOT A QUERY. S3 has no "size of this prefix" call. The only way to total a
-- bucket is to page ListObjectsV2 and sum every object's Size -- O(objects), 1,000 keys per
-- round trip. A tenant's datalake is parquet: thousands of files after a few months of daily
-- runs, so a tenant overview that computed it inline would take seconds and get slower every
-- night. app/storage.py::prefix_stats already concedes this, capping its scan at 5,000 objects
-- so a page load cannot stall -- which is the right call for a browser and the wrong one for a
-- total, because a capped total is just a wrong number.
--
-- Nor is there a portable admin API to lean on: RustFS's is quirky enough that
-- app/object_store.py had to reverse its wire format, and production runs an external object
-- store that will not have the same one. Listing is the only thing both ends implement.
--
-- So the totals are computed on demand, written here, and read from here. Staleness is
-- explicit -- `computed_at` is returned to the UI so it can say "as at", the same provenance
-- rule the client portal follows for valuations -- and `objects_truncated` records that a scan
-- hit its ceiling, so a partial total is never presented as a complete one.
-- ============================================================================

CREATE TABLE storage.tenant_usage (
  tenant_id         int PRIMARY KEY REFERENCES auth.tenants(id) ON DELETE CASCADE,
  -- Split by prefix because they answer different questions: the datalake is what the ETL
  -- costs us, files/ is what the firm and its clients have put there.
  datalake_bytes    bigint NOT NULL DEFAULT 0,
  datalake_objects  int NOT NULL DEFAULT 0,
  files_bytes       bigint NOT NULL DEFAULT 0,
  files_objects     int NOT NULL DEFAULT 0,
  -- A scan that hit its object ceiling. The totals are then lower bounds, and the UI must say
  -- so rather than draw a number that looks authoritative.
  objects_truncated boolean NOT NULL DEFAULT false,
  -- How long the scan took, so an operator can see the cache earning its keep.
  scan_ms           int,
  error             text,
  computed_at       timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE storage.tenant_usage IS
  'Cached ListObjectsV2 rollups per tenant bucket. Recomputed on demand '
  '(POST /admin/tenants/{id}/storage/usage/refresh); read with computed_at so staleness is visible.';

-- Operations-facing figures about the tenant's infrastructure, not tenant data, and written by
-- a background scan. service_role only -- no policy and no grant to `authenticated`, which is
-- the same choice 0038 made for aml.operator_secrets: withholding the privilege beats an
-- always-false policy someone later "fixes".
ALTER TABLE storage.tenant_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE storage.tenant_usage FORCE ROW LEVEL SECURITY;
GRANT ALL ON storage.tenant_usage TO service_role;
