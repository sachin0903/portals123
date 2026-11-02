"""aml: drop the Creditsafe mirror, refusing while any aml.* table holds a row

Revision ID: 0225_drop_aml_schema
Revises: 0224_compliance_document_back
Create Date: 2026-10-01
"""

from pathlib import Path

from alembic import op

revision: str = "0225_drop_aml_schema"
down_revision: str | None = "0224_compliance_document_back"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0225_drop_aml_schema.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    """Deliberately not reversible.

    The upgrade only ran on an empty schema, so there is no data to restore, and no code reads
    schema aml. Rebuilding it would mean replaying 0037 and 0038, and 0038 also replaces
    ``audit.record()``, which later migrations have replaced again.
    """
    raise NotImplementedError(
        "0225 is one-way: schema aml was empty when it was dropped and nothing reads it. "
        "Its records are in the object-storage export the guard asked for."
    )
