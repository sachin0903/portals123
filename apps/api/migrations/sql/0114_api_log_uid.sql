-- ============================================================================
-- 0114_api_log_uid.sql -- audit.api_log.uid, so the one remaining serial leaves a URL.
--
-- The admin console expands a row by fetching the call's bodies, and that path named the serial:
-- `/platforms/api/{id}` in the operations portal, onto `/admin/platforms/api-calls/{id}` in the
-- API. It was the single entry on `route-addressing.test.ts`'s allowlist -- the rule's own
-- exception -- and the reason it was allowed is that `audit.api_log` had no uid to use instead.
-- Now it does, and the allowlist is empty.
--
-- The objection to a serial is the same one messaging answered: it publishes the row count and
-- lets a caller walk the table by arithmetic. That the reader here holds `platforms:read` and
-- could list the calls anyway makes the leak uninteresting, not absent -- and an identifier's
-- shape is not a statement about today's callers.
--
-- Written for a table that is append-only and the largest audit surface we have, so the sequence
-- matters:
--
--   * ADD COLUMN with NO default and NULLable -- catalogue only, instant, no rewrite;
--   * then SET DEFAULT -- also catalogue only. Doing it the other way round (ADD COLUMN ... NOT
--     NULL DEFAULT nanoid(12)) would REWRITE THE WHOLE TABLE under an ACCESS EXCLUSIVE lock,
--     because Postgres' fast path for a defaulted column requires a non-volatile default and
--     nanoid is volatile by necessity;
--   * then backfill, which takes row locks and not a table lock;
--   * then the constraint and the index, behind a lock_timeout.
--
-- Not CONCURRENTLY, per the convention 0024 records: these migrations run inside Alembic's
-- transaction, which CONCURRENTLY cannot. The lock_timeout is what keeps the index build from
-- hanging behind a long-running read -- it fails fast instead, and the deploy is retried.
-- ============================================================================

ALTER TABLE audit.api_log ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE audit.api_log ALTER COLUMN uid SET DEFAULT public.nanoid(12);

-- The default only fires for rows inserted after it exists. `nanoid` is VOLATILE, so this is one
-- value per row rather than one for the table -- the unique index below is what would catch it if
-- that were ever untrue.
UPDATE audit.api_log SET uid = public.nanoid(12) WHERE uid IS NULL;

SET LOCAL lock_timeout = '5s';

ALTER TABLE audit.api_log ALTER COLUMN uid SET NOT NULL;

-- Unique because it is an address: two rows answering to one URL is the bug the column exists to
-- make impossible.
CREATE UNIQUE INDEX IF NOT EXISTS api_log_uid_key ON audit.api_log(uid);

COMMENT ON COLUMN audit.api_log.uid IS
  'Opaque public identifier -- what the operations console names when it opens one call '
  '(/platforms/api/{uid}). The bigint id stays the table''s own key and its sort order; see '
  'docs/api.md -> "Addressing".';
