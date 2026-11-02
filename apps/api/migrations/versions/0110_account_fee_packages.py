"""platform.account_fee_packages: equal many-to-many account fee assignments

Revision ID: 0110_account_fee_packages
Revises: 0109_account_role_attorney_other
Create Date: 2026-09-03
"""

from pathlib import Path

from alembic import op

revision: str = "0110_account_fee_packages"
down_revision: str | None = "0109_account_role_attorney_other"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0110_account_fee_packages.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.account_fee_packages")
    op.execute(
        "ALTER TABLE platform.fee_packages DROP CONSTRAINT IF EXISTS fee_packages_tenant_id_key"
    )
    op.execute("COMMENT ON COLUMN platform.accounts.fee_package_id IS NULL")
