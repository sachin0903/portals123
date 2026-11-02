"""audit.actions — the request-level action-event stream

Revision ID: 0041_action_events
Revises: 0040_file_types
Create Date: 2026-08-03

One row per successful authenticated mutating request, written best-effort by
app/audit_actions.py::ActionEventsMiddleware — the uniform "every action emits an event" floor under
the richer domain mechanisms, designed as a cursor-tailable stream so future subscribers can
trigger work off it. DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0041_action_events"
down_revision: str | None = "0040_file_types"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0041_action_events.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS audit.actions")
