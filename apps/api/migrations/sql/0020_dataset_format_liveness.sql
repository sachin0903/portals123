-- 0020_dataset_format_liveness: split the dataset `source_type` into two independent axes —
-- FORMAT (how the data is defined: sql | csv | inline) × LIVENESS (static snapshot | pulled live at
-- render) — and drop the 'questionnaire' source (a form *links* a dataset; it was never a data source
-- itself). Independent axes so the catalogue filters by format and by liveness separately.
--
-- Greenfield: datasets are dev-seeded only (no production rows), so this restructures in place rather
-- than the expand/contract dance a live table would need. `scope` (subject|collection|global) is
-- unchanged — a dataset stays a tenant catalogue asset, and 'subject' only parameterises the query by
-- the render's client (:subject_id). The inline-rows column `static_data` becomes `inline_data` to
-- match the new `inline` format.

CREATE TYPE documents.dataset_format   AS ENUM ('sql', 'csv', 'inline');
CREATE TYPE documents.dataset_liveness AS ENUM ('static', 'live');

-- FORCE ROW LEVEL SECURITY binds even the table owner (the migrator — it is NOT BYPASSRLS; see
-- 0001), so the backfill DELETE/UPDATE below would be RLS-filtered to zero rows while the DDL
-- SET NOT NULL still sees every physical row. Lift FORCE for the restructure, restore it at the end.
ALTER TABLE documents.datasets         NO FORCE ROW LEVEL SECURITY;
ALTER TABLE documents.dataset_versions NO FORCE ROW LEVEL SECURITY;

-- 'questionnaire' datasets (none in dev) carry no data source under the new model — remove them
-- (cascades to their versions) before the restructure so the new CHECK can't be violated.
DELETE FROM documents.datasets WHERE source_type = 'questionnaire';

-- ── datasets: source_type → (format, liveness) ────────────────────────────────
ALTER TABLE documents.datasets ADD COLUMN format   documents.dataset_format;
ALTER TABLE documents.datasets ADD COLUMN liveness documents.dataset_liveness;
UPDATE documents.datasets SET
    format = CASE source_type::text WHEN 'sql' THEN 'sql' WHEN 'csv' THEN 'csv' ELSE 'inline' END
             ::documents.dataset_format,
    liveness = CASE source_type::text WHEN 'sql' THEN 'live' ELSE 'static' END
               ::documents.dataset_liveness;
ALTER TABLE documents.datasets ALTER COLUMN format   SET NOT NULL;
ALTER TABLE documents.datasets ALTER COLUMN liveness SET NOT NULL;
ALTER TABLE documents.datasets DROP COLUMN source_type;

-- ── dataset_versions: source_type → format; static_data → inline_data; drop questionnaire ─────────
-- Drop the exactly-one-source CHECK first (it references source_type + questionnaire_uid).
DO $$
DECLARE c text;
BEGIN
    FOR c IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'documents.dataset_versions'::regclass AND contype = 'c'
    LOOP
        EXECUTE format('ALTER TABLE documents.dataset_versions DROP CONSTRAINT %I', c);
    END LOOP;
END $$;

ALTER TABLE documents.dataset_versions ADD COLUMN format documents.dataset_format;
UPDATE documents.dataset_versions SET
    format = CASE source_type::text WHEN 'sql' THEN 'sql' WHEN 'csv' THEN 'csv' ELSE 'inline' END
             ::documents.dataset_format;
ALTER TABLE documents.dataset_versions ALTER COLUMN format SET NOT NULL;
ALTER TABLE documents.dataset_versions DROP COLUMN source_type;
ALTER TABLE documents.dataset_versions DROP COLUMN questionnaire_uid;
ALTER TABLE documents.dataset_versions RENAME COLUMN static_data TO inline_data;

ALTER TABLE documents.dataset_versions ADD CONSTRAINT dataset_versions_source_ck CHECK (
    (format = 'csv'    AND csv_file_id IS NOT NULL AND sql_query IS NULL   AND inline_data IS NULL) OR
    (format = 'sql'    AND sql_query   IS NOT NULL AND csv_file_id IS NULL AND inline_data IS NULL) OR
    (format = 'inline' AND inline_data IS NOT NULL AND csv_file_id IS NULL AND sql_query IS NULL)
);

ALTER TABLE documents.datasets         FORCE ROW LEVEL SECURITY;
ALTER TABLE documents.dataset_versions FORCE ROW LEVEL SECURITY;

DROP TYPE documents.dataset_source;
