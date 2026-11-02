"""auth.merge_provider_user: sync a custodian's employee users, attaching by email

Revision ID: 0136_provider_users
Revises: 0135_product_platform_roles
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0136_provider_users"
down_revision: str | None = "0135_product_platform_roles"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0136_provider_users.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP FUNCTION IF EXISTS auth.merge_provider_user(regclass, int, text);"
    )
