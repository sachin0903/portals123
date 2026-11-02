"""add 'discretionary' to crm.service_type (client Service dropdown)

Revision ID: 0104_service_type_discretionary
Revises: 0103_app_secrets
Create Date: 2026-08-19
"""

from pathlib import Path

from alembic import op

revision: str = "0104_service_type_discretionary"
down_revision: str | None = "0103_app_secrets"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0104_service_type_discretionary.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Postgres cannot DROP an enum value; leaving 'discretionary' in place is harmless.
    pass
