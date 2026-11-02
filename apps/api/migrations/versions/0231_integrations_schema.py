"""The `integrations` schema (SEI platform approvals/DSRs/adviser map) + sei_reports' own tables

Revision ID: 0231_integrations_schema
Revises: 0230_transactions_rls_once
Create Date: 2026-10-05
"""

from pathlib import Path

from alembic import op

revision: str = "0231_integrations_schema"
down_revision: str | None = "0230_transactions_rls_once"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0231_integrations_schema.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP SCHEMA IF EXISTS integrations CASCADE")
