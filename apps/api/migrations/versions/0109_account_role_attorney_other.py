"""add attorney and other to crm.account_role (interested parties at kickoff)

Revision ID: 0109_account_role_attorney_other
Revises: 0108_drop_org_portal_columns
Create Date: 2026-08-21
"""

from pathlib import Path

from alembic import op

revision: str = "0109_account_role_attorney_other"
down_revision: str | None = "0108_drop_org_portal_columns"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0109_account_role_attorney_other.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Postgres cannot DROP an enum value; leaving attorney/other in place is harmless.
    pass
