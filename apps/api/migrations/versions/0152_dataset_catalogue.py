"""dataset catalogue: format becomes (catalogue|inline), liveness/sql_query/csv_file_id go

Revision ID: 0152_dataset_catalogue
Revises: 0151_product_role_cardinality
Create Date: 2026-09-04
"""

from pathlib import Path

from alembic import op

revision: str = "0152_dataset_catalogue"
down_revision: str | None = "0151_product_role_cardinality"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0152_dataset_catalogue.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Structural only. The retired columns come back EMPTY: their contents were the free-text SQL
    # and the CSV file reference, and nothing that could be restored survives the upgrade — which
    # is honest for a greenfield table with no rows, and would be data loss on one with any. The
    # upgrade refuses to run in that second case rather than relying on this.
    op.execute("ALTER TABLE documents.datasets         NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents.dataset_versions NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE documents.dataset_versions "
        "DROP CONSTRAINT IF EXISTS dataset_versions_catalogue_key_ck"
    )
    op.execute(
        "ALTER TABLE documents.dataset_versions DROP CONSTRAINT IF EXISTS dataset_versions_source_ck"
    )
    op.execute("ALTER TABLE documents.dataset_versions DROP COLUMN IF EXISTS catalogue_ref")
    op.execute("ALTER TABLE documents.dataset_versions ADD COLUMN sql_query text")
    op.execute(
        "ALTER TABLE documents.dataset_versions ADD COLUMN csv_file_id uuid "
        "REFERENCES documents.files(id)"
    )
    op.execute("CREATE TYPE documents.dataset_liveness AS ENUM ('static', 'live')")
    op.execute(
        "ALTER TABLE documents.datasets ADD COLUMN liveness documents.dataset_liveness "
        "NOT NULL DEFAULT 'static'"
    )
    op.execute("CREATE TYPE documents.dataset_format_old AS ENUM ('sql', 'csv', 'inline')")
    # Written out per table rather than looped: interpolating a table name into an UPDATE is the
    # shape a SQL-injection lint flags, and it cannot tell a hardcoded tuple from a parameter. Two
    # explicit blocks are also plainer to read in a migration than a loop over a pair.
    for stmt in (
        "ALTER TABLE documents.datasets ADD COLUMN format_old documents.dataset_format_old",
        "UPDATE documents.datasets SET format_old = "
        "CASE format::text WHEN 'inline' THEN 'inline' ELSE 'sql' END"
        "::documents.dataset_format_old",
        "ALTER TABLE documents.datasets DROP COLUMN format",
        "ALTER TABLE documents.datasets RENAME COLUMN format_old TO format",
        "ALTER TABLE documents.datasets ALTER COLUMN format SET NOT NULL",
        "ALTER TABLE documents.dataset_versions ADD COLUMN format_old documents.dataset_format_old",
        "UPDATE documents.dataset_versions SET format_old = "
        "CASE format::text WHEN 'inline' THEN 'inline' ELSE 'sql' END"
        "::documents.dataset_format_old",
        "ALTER TABLE documents.dataset_versions DROP COLUMN format",
        "ALTER TABLE documents.dataset_versions RENAME COLUMN format_old TO format",
        "ALTER TABLE documents.dataset_versions ALTER COLUMN format SET NOT NULL",
    ):
        op.execute(stmt)
    op.execute("DROP TYPE documents.dataset_format")
    op.execute("ALTER TYPE documents.dataset_format_old RENAME TO dataset_format")
    op.execute(
        "ALTER TABLE documents.dataset_versions ADD CONSTRAINT dataset_versions_source_ck CHECK ("
        "  (format = 'csv'    AND csv_file_id IS NOT NULL AND sql_query IS NULL"
        "                     AND inline_data IS NULL) OR"
        "  (format = 'sql'    AND sql_query   IS NOT NULL AND csv_file_id IS NULL"
        "                     AND inline_data IS NULL) OR"
        "  (format = 'inline' AND inline_data IS NOT NULL AND csv_file_id IS NULL"
        "                     AND sql_query IS NULL))"
    )
    op.execute("ALTER TABLE documents.datasets         FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents.dataset_versions FORCE ROW LEVEL SECURITY")
