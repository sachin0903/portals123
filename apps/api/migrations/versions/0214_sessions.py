"""live WorkOS sessions, copied by the events poller

Revision ID: 0214_sessions
Revises: 0213_page_views
Create Date: 2026-10-03

``alembic_version.version_num`` is ``varchar(32)``; this id is 13 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0214_sessions"
down_revision: str | None = "0213_page_views"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0214_sessions.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP INDEX IF EXISTS auth.users_sessions_unsynced_idx;
        ALTER TABLE auth.users DROP COLUMN IF EXISTS sessions_synced_at;
        DROP TABLE IF EXISTS auth.sessions;
        """
    )
