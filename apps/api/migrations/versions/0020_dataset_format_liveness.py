"""split dataset source_type into format x liveness; drop questionnaire source

Revision ID: 0020_dataset_format_liveness
Revises: 0019_template_form_snapshot
Create Date: 2026-07-17

A dataset is now characterised by two independent axes — FORMAT (sql|csv|inline) and LIVENESS
(static|live) — instead of the single `source_type` enum, and 'questionnaire' is no longer a data
source. `scope` is unchanged. DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020_dataset_format_liveness"
down_revision: str | None = "0019_template_form_snapshot"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Reverse the split: rebuild the single `source_type` enum + columns (data-preserving for the three
# surviving formats; the dropped 'questionnaire' rows are not restored). Best-effort, greenfield.
_DOWN = """
CREATE TYPE documents.dataset_source AS ENUM ('csv', 'sql', 'static', 'questionnaire');

ALTER TABLE documents.datasets         NO FORCE ROW LEVEL SECURITY;
ALTER TABLE documents.dataset_versions NO FORCE ROW LEVEL SECURITY;

ALTER TABLE documents.datasets ADD COLUMN source_type documents.dataset_source;
UPDATE documents.datasets SET source_type =
    CASE format::text WHEN 'sql' THEN 'sql' WHEN 'csv' THEN 'csv' ELSE 'static' END
    ::documents.dataset_source;
ALTER TABLE documents.datasets ALTER COLUMN source_type SET NOT NULL;
ALTER TABLE documents.datasets DROP COLUMN format;
ALTER TABLE documents.datasets DROP COLUMN liveness;

ALTER TABLE documents.dataset_versions DROP CONSTRAINT IF EXISTS dataset_versions_source_ck;
ALTER TABLE documents.dataset_versions RENAME COLUMN inline_data TO static_data;
ALTER TABLE documents.dataset_versions ADD COLUMN source_type documents.dataset_source;
UPDATE documents.dataset_versions SET source_type =
    CASE format::text WHEN 'sql' THEN 'sql' WHEN 'csv' THEN 'csv' ELSE 'static' END
    ::documents.dataset_source;
ALTER TABLE documents.dataset_versions ALTER COLUMN source_type SET NOT NULL;
ALTER TABLE documents.dataset_versions ADD COLUMN questionnaire_uid text;
ALTER TABLE documents.dataset_versions DROP COLUMN format;
ALTER TABLE documents.dataset_versions ADD CONSTRAINT dataset_versions_check CHECK (
    (source_type = 'csv'           AND csv_file_id       IS NOT NULL AND sql_query IS NULL AND static_data IS NULL AND questionnaire_uid IS NULL) OR
    (source_type = 'sql'           AND sql_query         IS NOT NULL AND csv_file_id IS NULL AND static_data IS NULL AND questionnaire_uid IS NULL) OR
    (source_type = 'static'        AND static_data       IS NOT NULL AND csv_file_id IS NULL AND sql_query IS NULL AND questionnaire_uid IS NULL) OR
    (source_type = 'questionnaire' AND questionnaire_uid IS NOT NULL AND csv_file_id IS NULL AND sql_query IS NULL AND static_data IS NULL)
);

ALTER TABLE documents.datasets         FORCE ROW LEVEL SECURITY;
ALTER TABLE documents.dataset_versions FORCE ROW LEVEL SECURITY;

DROP TYPE documents.dataset_format;
DROP TYPE documents.dataset_liveness;
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0020_dataset_format_liveness.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
