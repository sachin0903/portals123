"""per-tenant document-type tag registry (open-ended vocabulary)

Revision ID: 0039_document_types
Revises: 0038_aml_operator_credentials
Create Date: 2026-07-29

documents.document_type is free text; this adds documents.document_types — the per-tenant set of
KNOWN tags that back the upload/reclassify pickers and auto-classify. Open-ended, not an allowlist:
the app registers any new tag on the fly (app/document_types.py). Seeds the common adviser types for
existing tenants and installs an AFTER INSERT trigger to seed future ones. DDL in the sibling SQL
file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0039_document_types"
down_revision: str | None = "0038_aml_operator_credentials"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0039_document_types.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP TRIGGER IF EXISTS seed_document_types ON auth.tenants;"
        "DROP FUNCTION IF EXISTS documents.seed_default_document_types();"
        "DROP TABLE IF EXISTS documents.document_types;"
    )
