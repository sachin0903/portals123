-- ============================================================================
-- messaging.{conversations,groups,attachments}.uid -- the three messaging rows a URL names.
--
-- A portal URL never carries a serial id (docs/api.md -> "Addressing"), and messaging shipped
-- addressing all three by `id`: `/chats/{id}`, `/chats/groups/{id}` and the two attachment
-- endpoints. Those routes are internal `+server.ts` proxies rather than pages, which is why the
-- rule's static scan was the thing that noticed -- but "no page links to it" is a statement about
-- today's callers, not about the identifier, and the whole point of an opaque handle is that a
-- serial one leaks the row count and lets a caller walk the table by arithmetic.
--
-- Messages themselves get NO uid: nothing addresses one. A uid exists to be put in a URL, and a
-- column added on the chance that something might is a column nobody maintains.
--
-- NO prefix and NO per-column default here. The first draft of this migration had both: a
-- `cnv_`/`grp_`/`att_` prefix over 16 hex characters, matching a local `_uid()` in
-- app/messaging.py. Both are wrong for the same reason -- they are a second answer to a question
-- `app/ids.py` already answers. A uid is nanoid(12) with no prefix (nothing resolves a uid without
-- already knowing its table, so the prefix labels a distinction no reader needs), and the DEFAULT
-- belongs to `0113_uid_defaults`, which states it once for every uid column rather than once per
-- table that happens to have several writers.
-- ============================================================================

ALTER TABLE messaging.conversations
  ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE messaging.groups
  ADD COLUMN IF NOT EXISTS uid text;
ALTER TABLE messaging.attachments
  ADD COLUMN IF NOT EXISTS uid text;

-- Backfill. `nanoid` is VOLATILE, so this is one value per row and not one for the table -- the
-- unique index below is what would catch it if that were ever untrue.
--
-- AS service_role, and that is not decoration -- it is the trap `0115_catalogue_uids` records,
-- met here first. All three of these tables are FORCE ROW LEVEL SECURITY (0080, 0081, 0084) and
-- their policies are `TO authenticated`, which the migrator is a member of while carrying no
-- BYPASSRLS of its own. So as the migrator this UPDATE matches NO rows -- the policies test
-- `auth.tenant_lineage()`/`auth.contact_scope()`, and a migration has no claims to satisfy them
-- with -- it fills nothing, and the SET NOT NULL below fails with "column uid contains null
-- values". Only on a database that HAS conversations, which a fresh one does not: this migration
-- passed every empty scratch database and failed the moment it met a deployment with a real chat
-- in it. The DDL either side stays as the migrator, which owns these tables; service_role cannot
-- ALTER them.
SET LOCAL ROLE service_role;

UPDATE messaging.conversations SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE messaging.groups        SET uid = public.nanoid(12) WHERE uid IS NULL;
UPDATE messaging.attachments   SET uid = public.nanoid(12) WHERE uid IS NULL;

RESET ROLE;

-- lock_timeout, per the repository's migration rule: SET NOT NULL takes an ACCESS EXCLUSIVE lock
-- while it validates. These tables are small, so the scan is quick -- the lock WAIT behind a
-- long-running read is what would stall a deploy.
SET LOCAL lock_timeout = '5s';

ALTER TABLE messaging.conversations ALTER COLUMN uid SET NOT NULL;
ALTER TABLE messaging.groups        ALTER COLUMN uid SET NOT NULL;
ALTER TABLE messaging.attachments   ALTER COLUMN uid SET NOT NULL;

-- Unique, because it is an address: two rows answering to one URL is the bug this column exists to
-- make impossible. Plain (not CONCURRENTLY) -- these tables are new and small, and the migration
-- runs in a transaction, which CONCURRENTLY cannot.
CREATE UNIQUE INDEX IF NOT EXISTS conversations_uid_key ON messaging.conversations(uid);
CREATE UNIQUE INDEX IF NOT EXISTS groups_uid_key        ON messaging.groups(uid);
CREATE UNIQUE INDEX IF NOT EXISTS attachments_uid_key   ON messaging.attachments(uid);

COMMENT ON COLUMN messaging.conversations.uid IS
  'Opaque public identifier -- what appears in a portal path (/chats/{uid}). The serial id stays '
  'the API''s and the schema''s own key; see docs/api.md -> "Addressing".';
COMMENT ON COLUMN messaging.groups.uid IS
  'Opaque public identifier -- what appears in a portal path (/chats/groups/{uid}).';
COMMENT ON COLUMN messaging.attachments.uid IS
  'Opaque public identifier -- what appears in an attachment download path.';
