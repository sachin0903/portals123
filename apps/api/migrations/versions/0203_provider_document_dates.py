"""Date a provider document from the custodian message, not the copy

Revision ID: 0203_provider_document_dates
Revises: 0202_sipp_scheme_admin
Create Date: 2026-09-23

First applied as ``0185_provider_document_dates``, then as ``0194_provider_document_dates``.
"""

from pathlib import Path

from alembic import op

revision: str = "0203_provider_document_dates"
down_revision: str | None = "0202_sipp_scheme_admin"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0203_provider_document_dates.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The dates have already moved to the message clock. Restoring the function
    # that stamped now() would not put the copy time back.
    pass
