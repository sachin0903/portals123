-- Retire the unbuilt document-versioning footprint in documents.documents:
--   * the `superseded_by` self-FK column (0005) — written by nothing, read by nothing;
--   * the `superseded` status value — written by nothing, only ever EXCLUDED on read;
--   * the `generating` status value — never written (the worker goes queued -> generated).
-- Verified dead across the codebase (no INSERT/UPDATE sets them; the four read filters that named
-- 'superseded' are stripped in the SAME change as this migration, because a query naming a
-- now-invalid enum literal would error). See docs/audit/02-documents.md. The live reassign flow's
-- `document.agreement_superseded` audit ACTION string is unrelated and untouched.
--
-- Contract migration. Postgres cannot drop an ENUM value in place, so the type is rebuilt, which
-- rewrites documents.documents (its status index is rebuilt automatically). Bounded by lock_timeout;
-- a precheck fails loudly rather than letting the USING cast silently blow up, though it is a no-op
-- because no row holds a removed value.

SET LOCAL lock_timeout = '5s';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM documents.documents WHERE status IN ('superseded', 'generating')) THEN
        RAISE EXCEPTION
            'documents.documents holds rows with status superseded/generating; cannot retire these values';
    END IF;
END $$;

-- 1. Drop the dead self-FK column.
ALTER TABLE documents.documents DROP COLUMN IF EXISTS superseded_by;

-- 2. Rebuild the status enum without 'superseded'/'generating'. The DEFAULT is typed to the old
--    enum, so drop it, swap the type (no-op USING cast), restore the default, drop the old type.
ALTER TABLE documents.documents ALTER COLUMN status DROP DEFAULT;
ALTER TYPE documents.document_status RENAME TO document_status_old;
CREATE TYPE documents.document_status AS ENUM ('queued', 'generated', 'failed', 'archived');
ALTER TABLE documents.documents
    ALTER COLUMN status TYPE documents.document_status
    USING status::text::documents.document_status;
ALTER TABLE documents.documents ALTER COLUMN status SET DEFAULT 'queued';
DROP TYPE documents.document_status_old;
