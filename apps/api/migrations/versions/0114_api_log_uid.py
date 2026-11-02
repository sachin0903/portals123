"""audit.api_log.uid -- the last serial in a portal URL

Revision ID: 0114_api_log_uid
Revises: 0113_uid_defaults
Create Date: 2026-08-25

The operations console opened one API call by its serial (`/platforms/api/{id}`), the single entry
on route-addressing.test.ts's allowlist, because the table had no uid to use instead. Added
NULLable-then-defaulted rather than `NOT NULL DEFAULT` in one step: nanoid is volatile, so the
one-step form would rewrite the largest audit table we have under an exclusive lock.
"""

from pathlib import Path

from alembic import op

revision: str = "0114_api_log_uid"
down_revision: str | None = "0113_uid_defaults"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0114_api_log_uid.sql")


def downgrade() -> None:
    # The column goes with its index. Nothing else references it: the uid is read back and put in a
    # path, never stored anywhere else.
    op.get_bind().exec_driver_sql("DROP INDEX IF EXISTS audit.api_log_uid_key")
    op.get_bind().exec_driver_sql("ALTER TABLE audit.api_log DROP COLUMN IF EXISTS uid")
