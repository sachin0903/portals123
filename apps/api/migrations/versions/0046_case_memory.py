"""case memory — the typed key-value store a v3 journey carries between its nodes

Revision ID: 0046_case_memory
Revises: 0045_flow_timers
Create Date: 2026-08-04

Flows v3 Phase B: `flows.case_memory`, one row per (case, key). A table rather than a jsonb blob
on the case so concurrent writes cannot lose each other, so `audit.attach` records who set the
value a branch later tested, and so the size cap is a CHECK rather than a comment. Also widens
`flows.cases.initiation` with 'flow' — a journey started by another journey's `start_flow` node.
Expand only. DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0046_case_memory"
down_revision: str | None = "0045_flow_timers"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0046_case_memory.sql")


def downgrade() -> None:
    # Dropping this loses every running journey's variables, which is safe only because a
    # downgrade implies the graph engine that reads them is going too. The initiation CHECK is
    # narrowed back to 0043's set; any 'flow' row would fail it, so they are relabelled first.
    op.get_bind().exec_driver_sql(
        "DROP TABLE IF EXISTS flows.case_memory; "
        "UPDATE flows.cases SET initiation = 'automation' WHERE initiation = 'flow'; "
        "ALTER TABLE flows.cases DROP CONSTRAINT IF EXISTS cases_initiation_check; "
        "ALTER TABLE flows.cases ADD CONSTRAINT cases_initiation_check "
        "  CHECK (initiation IN ('adviser', 'client', 'automation'))"
    )
