"""generic orchestration model: executions group, polymorphic-subject cases, domain-event log + outbox

Revision ID: 0025_flows_orchestration
Revises: 0024_flows_p0_hardening
Create Date: 2026-07-23
"""

from pathlib import Path

from alembic import op

revision: str = "0025_flows_orchestration"
down_revision: str | None = "0024_flows_p0_hardening"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_DOWN = """
DROP TABLE IF EXISTS flows.outbox;
DROP TABLE IF EXISTS flows.domain_events;
ALTER TABLE flows.case_steps
  DROP COLUMN IF EXISTS step_key,
  DROP COLUMN IF EXISTS participant_subject_id,
  DROP COLUMN IF EXISTS depends_on,
  DROP COLUMN IF EXISTS scope;
ALTER TABLE flows.case_steps DROP CONSTRAINT IF EXISTS case_steps_status_check;
ALTER TABLE flows.case_steps ADD CONSTRAINT case_steps_status_check
  CHECK (status IN ('pending', 'fulfilled', 'skipped', 'cancelled'));
ALTER TABLE flows.cases
  DROP COLUMN IF EXISTS execution_id,
  DROP COLUMN IF EXISTS kind,
  DROP COLUMN IF EXISTS subject_type,
  DROP COLUMN IF EXISTS subject_id;
DROP TABLE IF EXISTS flows.executions;
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0025_flows_orchestration.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
