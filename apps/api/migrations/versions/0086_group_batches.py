"""tag a group send's per-member rows with a shared batch id

Revision ID: 0086_group_batches
Revises: 0085_group_scoped_messages
Create Date: 2026-08-07

Without this, a group's feed had no way to tell "three people each got their own copy of the
same broadcast" apart from "three unrelated messages that happen to share text" — it rendered
every fanned-out row as its own bubble, so a 3-member group made a single send look like three.
`batch_id` ties the N rows one `send_group_message` call creates together explicitly.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0086_group_batches"
down_revision: str | None = "0085_group_scoped_messages"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text().replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0086_group_batches.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "ALTER TABLE messaging.messages DROP COLUMN IF EXISTS batch_id;"
    )
