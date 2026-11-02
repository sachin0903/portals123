-- platform.fee_packages: a unique key on the provider's own id, so the fee register can be
-- SYNCED rather than only typed in.
--
-- 0002 gave fee_packages an `external_id` column and no constraint on it, because at the time
-- nothing populated it. The Seccl fee-structure sync now does
-- (`GET /v1/fee-structures/{firmId}` -> `platform.fee_packages` in the lake ->
-- `app/materialise.py::_sync_fee_packages`), and an upsert needs a key: without one, every
-- nightly run would INSERT the firm's charges again, and a firm would end the week with seven
-- copies of its platform fee, each of them selectable on an account.
--
-- Keyed on (tenant_id, external_id) and NOT on the name, for the reason 0046 had to learn on
-- transfer_providers: a provider register holds the same NAME twice for two genuinely different
-- rows, and keying on it silently overwrote one with the other. A fee structure's identity is
-- the id the custodian prices against.
--
-- PARTIAL, on `external_id IS NOT NULL`. A hand-authored package has no provider id and there
-- may be many of them; only the synced rows are claiming to be a specific upstream object.
-- (Postgres would allow multiple NULLs under a plain UNIQUE anyway -- the WHERE clause is there
-- to say so deliberately, and to keep the index off the rows that will never be looked up
-- through it.)
--
-- Deduplicate first: no environment has ever written external_id here, so this is a no-op on
-- every real database, but an index that could meet duplicates has to say what happens to them.
-- The lowest id wins -- the earliest row is the one anything else may already reference.
DELETE FROM platform.fee_packages f
 USING platform.fee_packages keep
 WHERE f.tenant_id = keep.tenant_id
   AND f.external_id = keep.external_id
   AND f.external_id IS NOT NULL
   AND f.id > keep.id;

-- Not CONCURRENTLY, for the reason 0044 gives on platform.products: a firm's fee register is
-- single digits to low tens of rows, and a migration transaction cannot build an index
-- concurrently anyway. `lock_timeout` still bounds the wait rather than queueing behind a long
-- reader.
SET LOCAL lock_timeout = '5s';

CREATE UNIQUE INDEX fee_packages_tenant_external_key
  ON platform.fee_packages (tenant_id, external_id)
  WHERE external_id IS NOT NULL;

COMMENT ON COLUMN platform.fee_packages.external_id IS
  'The provider''s own fee-structure id (a Seccl fee-structure _id). Set by the sync; NULL for '
  'a package authored in the admin catalogue. Unique per tenant when present, so the sync '
  'upserts rather than duplicating.';
