-- ============================================================================
-- Re-point platform.tenant_platforms' COMMENT at code that exists.
--
-- 0048 set it to "... See app/fanout.py and docs/provider-routing.md." — and `app/fanout.py` has
-- since been deleted. Nothing overwrote the comment, so `\d+ platform.tenant_platforms` in
-- production still sends whoever runs it to a file that is not in the repository.
--
-- A table comment is documentation that ships INSIDE the database, which is exactly why it goes
-- stale unnoticed: no gate reads it, no test asserts it, and it outlives the code it names.
-- ============================================================================
COMMENT ON TABLE platform.tenant_platforms IS
  'Which providers a firm is enabled on, and the slice of the core warehouse that is theirs. '
  'The per-tenant chain that reads it lives in each provider''s own interlace project '
  '(pkgs/book-sink); see docs/provider-pipeline.md. `last_fanout_at` is stamped by the merge.';
