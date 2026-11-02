"""flow engine hardening: case_steps.data snapshot + one-active-case-per-subject index

Revision ID: 0023_flows_hardening
Revises: 0022_request_by_nullable
Create Date: 2026-07-23
"""

from pathlib import Path

from alembic import op

revision: str = "0023_flows_hardening"
down_revision: str | None = "0022_request_by_nullable"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_DOWN = """
DROP INDEX IF EXISTS flows.cases_one_active_per_subject;
ALTER TABLE flows.case_steps DROP COLUMN IF EXISTS data;
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0023_flows_hardening.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
