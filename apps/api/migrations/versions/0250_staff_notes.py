"""staff notes: a member of staff's own checklist, kept beside the book on the dashboard

Revision ID: 0250_staff_notes
Revises: 0249_email_allowlist
Create Date: 2026-10-12

The dashboard rail's notes were a browser-local list; a list that does not follow its owner to
the next machine was the defect. Personal (owner-only RLS), tenant-scoped, not a task.
"""

from pathlib import Path

from alembic import op

revision: str = "0250_staff_notes"
down_revision: str | None = "0249_email_allowlist"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0250_staff_notes.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s'; DROP TABLE IF EXISTS platform.staff_notes;"
    )
