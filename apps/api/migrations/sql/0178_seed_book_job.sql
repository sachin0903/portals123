-- 0178_seed_book_job: a tenant-data job that replaces only the demo client book.
--
-- reset / reset_and_seed still wipe operational data. seed_book deletes the previous
-- seeded_demo clients/accounts/transactions/holdings and writes them again, without
-- touching real clients, documents, staff or authored flows.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.tenant_data_jobs
  DROP CONSTRAINT IF EXISTS tenant_data_jobs_kind_check;

ALTER TABLE platform.tenant_data_jobs
  ADD CONSTRAINT tenant_data_jobs_kind_check
  CHECK (kind IN ('reset', 'reset_and_seed', 'seed_book'));

COMMENT ON TABLE platform.tenant_data_jobs IS
  'Operations-only ledger for non-production tenant data reset, demo seed, and book-only reseed jobs.';
