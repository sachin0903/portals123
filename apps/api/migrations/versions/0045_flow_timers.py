"""flow timers — durable wake-ups for `wait`, plus the listener half of `wait_for_event`

Revision ID: 0045_flow_timers
Revises: 0044_tags
Create Date: 2026-08-04

Flows v3 Phase B: time as a committed row rather than an in-process sleep, so a redeployed worker
cannot lose a client's seven-day chaser. Two tables — flows.timers (a pending wake-up) and
flows.case_listeners (a case parked on a trigger, scoped to its own subject). Expand only. DDL in
the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0045_flow_timers"
down_revision: str | None = "0044_tags"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0045_flow_timers.sql")


def downgrade() -> None:
    # Dropping these strands any case parked on a wait node — which is safe only because a
    # downgrade implies the graph engine that parks them is going too. Listeners first: they
    # reference timers.
    op.get_bind().exec_driver_sql(
        "DROP TABLE IF EXISTS flows.case_listeners; DROP TABLE IF EXISTS flows.timers"
    )
