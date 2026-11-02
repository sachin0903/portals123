-- ============================================================================
-- 0066_links_tenant_key -- a link is a TENANT's mapping, so its key says so.
--
-- 0003 keyed platform.links on (provider, entity_type, entity_id, link_type). For a tenant-scoped
-- entity that is already unique per tenant, because the entity_id implies the tenant. For a SHARED
-- entity it is wrong, and platform.instruments is shared on purpose -- "global, deliberately
-- un-RLS'd: an ISIN is not tenant data" (0031, data-architecture Decision 1).
--
-- So the second firm to hold a fund could not record that it holds it:
--
--   * the mapping (tenant B, instrument 42, seccl) collides with (tenant A, instrument 42, seccl),
--     so the insert is skipped or -- worse, in app/materialise.py's ON CONFLICT DO UPDATE -- tenant
--     A's row is rewritten with tenant B's external id;
--   * the merge then finds no link for tenant B and creates a SECOND instrument, which
--     instruments_isin_key refuses, and the whole entity fails for that firm.
--
-- Latent until now only because one tenant on dev is enabled for Seccl. It surfaced the first time
-- two tenants merged the same ISIN in a test, which is the cheapest place to find it.
--
-- The new key is a RELAXATION for every scoped entity (same uniqueness, one more column) and the
-- fix for the shared ones. Written plainly rather than with CREATE UNIQUE INDEX CONCURRENTLY: this
-- table holds one row per provider mapping (thousands, not millions), and a concurrent build cannot
-- run inside Alembic's transaction. lock_timeout keeps a wedged deploy from queueing behind it.
-- ============================================================================

SET lock_timeout = '5s';

ALTER TABLE platform.links DROP CONSTRAINT links_unique_link;
ALTER TABLE platform.links
  ADD CONSTRAINT links_unique_link UNIQUE (tenant_id, provider, entity_type, entity_id, link_type);

COMMENT ON CONSTRAINT links_unique_link ON platform.links IS
  'One mapping per tenant per provider per entity. tenant_id is in the key because a SHARED entity '
  '(platform.instruments) is legitimately mapped by several tenants at once.';

-- ============================================================================
-- book_writer needs two more things, both read-shaped and both discovered by wiring the pipeline
-- up rather than by reasoning about it.
-- ============================================================================

-- Which bucket a tenant's own lake lives in. The pipeline reads it at project load to decide which
-- tenants have models at all: a tenant with no ACTIVE bucket has nowhere to put its parquet, and
-- projecting it into the book while its lake goes nowhere is the half-landed state the storage
-- registry exists to prevent. The SECRETS stay out of reach -- storage.tenant_storage_secrets and
-- storage.tenant_service_user_secrets are service_role-only and are not granted here.
GRANT USAGE ON SCHEMA storage TO book_writer;
GRANT SELECT ON storage.tenants TO book_writer;

-- The admin tenant page reads `last_fanout_at` as "last fan-out". A pipeline that wrote the book and
-- left the timestamp alone reads as one that never ran.
GRANT UPDATE ON platform.tenant_platforms TO book_writer;
