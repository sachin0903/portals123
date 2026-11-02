"""documents.requests.requested_by nullable (client-initiated flow cases have no staff requester)

Revision ID: 0022_request_by_nullable
Revises: 0021_flows
Create Date: 2026-07-22
"""

from pathlib import Path

from alembic import op

revision: str = "0022_request_by_nullable"
down_revision: str | None = "0021_flows"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Re-adding NOT NULL is only safe if no NULLs exist; best-effort for a greenfield down-migration.
_DOWN = "ALTER TABLE documents.requests ALTER COLUMN requested_by SET NOT NULL;"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0022_request_by_nullable.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
