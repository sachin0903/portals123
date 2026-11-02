-- ============================================================================
-- 0182_spotlight_search — index the words people use to find daily work.
--
-- Search still reads the source tables under their existing RLS policies. There
-- is deliberately no denormalised search catalogue: copying a document, task,
-- conversation or review into one would mean copying every source table's
-- visibility rules too, and one missed rule would be a data leak.
--
-- The indexes themselves are created CONCURRENTLY by the Alembic wrapper (they
-- cannot run in this transaction). This file owns only the extension and the
-- immutable expression both the indexes and app/routers/search.py call.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS search;

CREATE OR REPLACE FUNCTION search.normalise(VARIADIC parts text[])
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
RETURN lower(
  btrim(
    regexp_replace(
      array_to_string(parts, ' ', ''),
      '[[:space:]]+',
      ' ',
      'g'
    )
  )
);

CREATE OR REPLACE FUNCTION search.has_prefix(pattern text, VARIADIC parts text[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
RETURN EXISTS (
  SELECT 1
  FROM unnest(parts) AS part
  WHERE lower(part) LIKE pattern ESCAPE '\'
);

GRANT USAGE ON SCHEMA search TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION search.normalise(VARIADIC text[]) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION search.has_prefix(text, VARIADIC text[])
  TO authenticated, service_role;

COMMENT ON FUNCTION search.normalise(VARIADIC text[]) IS
  'One immutable folding expression for Spotlight GIN indexes and their matching predicates. '
  'It joins nullable source fields, lowercases and collapses whitespace; source-table RLS remains '
  'the visibility boundary.';

COMMENT ON FUNCTION search.has_prefix(text, VARIADIC text[]) IS
  'True when any supplied field starts with an escaped lower-case LIKE pattern. Used for useful '
  'two-character matching without enabling broad fuzzy search.';
