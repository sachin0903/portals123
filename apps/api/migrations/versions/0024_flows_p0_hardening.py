"""flow engine phase-0 hardening: re-key active-case guard, FK indexes, public throttle

Revision ID: 0024_flows_p0_hardening
Revises: 0023_flows_hardening
Create Date: 2026-07-23
"""

from pathlib import Path

from alembic import op

revision: str = "0024_flows_p0_hardening"
down_revision: str | None = "0023_flows_hardening"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_DOWN = """
DROP TABLE IF EXISTS flows.public_start_throttle;
DROP INDEX IF EXISTS flows.case_steps_account_id_idx;
DROP INDEX IF EXISTS flows.flows_current_published_version_idx;
DROP INDEX IF EXISTS flows.cases_flow_version_id_idx;
DROP INDEX IF EXISTS flows.cases_one_active_per_subject;
CREATE UNIQUE INDEX cases_one_active_per_subject
  ON flows.cases (flow_id, subject_contact_id)
  WHERE subject_contact_id IS NOT NULL AND status NOT IN ('completed', 'cancelled');
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0024_flows_p0_hardening.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
