-- ============================================================================
-- platform.tenant_platforms.last_merged_at -- what the column has always meant.
--
-- EXPAND PHASE. `last_fanout_at` was named for the fan-out, a step in the three-hop path that has
-- been deleted: the projection into a tenant's lake and the write into the book both happen inside
-- the provider's own interlace DAG now, and the value is stamped by the MERGE
-- (`book_sink.merge.merge_tenant`). The word outlived the thing it named, and "fan-out" has since
-- come to mean three different things in this repository -- the deleted step, this column, and
-- Seccl's own per-entity detail pulls (`services/seccl/models/raw_fanout.py`).
--
-- Both columns exist for one release. The writer sets both; readers prefer the new one and fall
-- back. The contract phase drops `last_fanout_at` once nothing reads it, which is the repository's
-- standing rule for a column rename (CLAUDE.md: migrations are expand/contract).
--
-- Backfilled from the old column, so a firm that merged last night does not read as "never".
-- ============================================================================
ALTER TABLE platform.tenant_platforms
  ADD COLUMN IF NOT EXISTS last_merged_at timestamptz;

UPDATE platform.tenant_platforms
   SET last_merged_at = last_fanout_at
 WHERE last_merged_at IS NULL AND last_fanout_at IS NOT NULL;

COMMENT ON COLUMN platform.tenant_platforms.last_merged_at IS
  'When this firm''s slice was last merged into the book by pkgs/book-sink. Supersedes '
  'last_fanout_at, which is written in parallel until the contract phase drops it.';

-- book_writer already holds UPDATE on this table (0066); a new column needs no further grant.
