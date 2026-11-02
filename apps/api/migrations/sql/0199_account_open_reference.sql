-- 0199_account_open_reference — the per-product opening reference a custodian needs to
-- CREATE an account, which is a different id from the one the pipeline READS.
--
-- SEI: `accountList[].accountTemplateId`. Required by POST /accountservice/accounts, appears
-- in no SFTP report and under no Reference TYPE. Discovered by `ref.enrollment_templates`
-- (API-only); which template belongs to which shelf product is an operator judgement.
--
-- Seccl: `wrapperDetail.schemeProductId`, required to open a PENSION. Same grain, same
-- question in different words — one column closes both.
--
-- Why not the columns that were already there:
--   external_id  is the platform's reference for the product as the pipeline READS it
--                (Seccl's wrapper type, SEI's ACCT_TYPE). The open reference is what a
--                write SENDS. Different values, different lifetimes.
--   data jsonb   would work and is wrong. An account cannot be opened without this, so it
--                is a documented part of the contract rather than provider miscellanea.
--
-- An earlier draft of docs/api-integrations.md numbered this 0116; that revision was spent
-- on comms_messages. The column is new here.
--
-- Nullable, no default, no backfill: there is no value to guess. The book-sink merge only
-- updates `external_id` on this table, so a template an operator attached survives the
-- nightly projection.
--
-- Expand-only. The migrator is not BYPASSRLS; ADD COLUMN needs no DML.

SET LOCAL lock_timeout = '5s';

ALTER TABLE platform.product_platforms
  ADD COLUMN IF NOT EXISTS account_open_reference text;

COMMENT ON COLUMN platform.product_platforms.account_open_reference IS
  'The reference a write SENDS to open an account at this provider: SEI accountTemplateId, '
  'Seccl wrapperDetail.schemeProductId. Distinct from external_id, which is the reference '
  'the pipeline READS. Set from the admin products page; never inferred.';
