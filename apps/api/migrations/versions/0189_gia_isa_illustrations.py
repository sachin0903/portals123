"""GIA and ISA illustration reference data and stored outputs

Revision ID: 0189_gia_isa_illustration
Revises: 0188_journey_assignee
Create Date: 2026-09-28
"""

from pathlib import Path

from alembic import op

revision: str = "0189_gia_isa_illustration"
down_revision: str | None = "0188_journey_assignee"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0189_gia_isa_illustrations.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql.replace("%", "%%"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP TABLE IF EXISTS platform.instrument_costs;
        DROP TABLE IF EXISTS platform.illustration_fee_schedules;
        DROP TABLE IF EXISTS platform.illustration_custody;
        DROP TABLE IF EXISTS platform.illustration_isa_allowances;
        DROP TABLE IF EXISTS platform.illustration_growth_sets;
        ALTER TABLE platform.illustrations
          DROP COLUMN IF EXISTS outputs,
          DROP COLUMN IF EXISTS reference_snapshot;
        ALTER TABLE auth.tenants
          DROP COLUMN IF EXISTS gia_isa_illustrations,
          DROP COLUMN IF EXISTS illustration_warning,
          DROP COLUMN IF EXISTS registered_office,
          DROP COLUMN IF EXISTS legal_name;
        """
    )
