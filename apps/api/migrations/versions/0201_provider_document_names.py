"""Move a re-filed provider document onto a corrected object key

Revision ID: 0201_provider_document_names
Revises: 0200_provider_documents
Create Date: 2026-09-22

First applied as ``0183_provider_document_names``, then as ``0192_provider_document_names``.
"""

from pathlib import Path

from alembic import op

revision: str = "0201_provider_document_names"
down_revision: str | None = "0200_provider_documents"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0201_provider_document_names.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The objects have already moved to the single name. Restoring the function
    # that ignored a new key would not put the doubled names back.
    pass
