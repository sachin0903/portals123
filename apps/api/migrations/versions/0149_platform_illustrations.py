"""platform.illustrations: a generated illustration as its own record, linked to the PDF

Revision ID: 0149_platform_illustrations
Revises: 0148_transfer_basis
Create Date: 2026-09-10
"""

from pathlib import Path

from alembic import op

revision: str = "0149_platform_illustrations"
down_revision: str | None = "0148_transfer_basis"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0149_platform_illustrations.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS platform.illustrations;")
