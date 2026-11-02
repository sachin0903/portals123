"""register the operations/admin portal host (app-based admin); resolve_claims stays app-preferred

Revision ID: 0102_operations_host
Revises: 0101_resolve_claims_multi_env
Create Date: 2026-08-19
"""

from pathlib import Path

from alembic import op

revision: str = "0102_operations_host"
down_revision: str | None = "0101_resolve_claims_multi_env"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0102_operations_host.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE auth.tenant_environments DROP COLUMN IF EXISTS operations_host;"
    )
