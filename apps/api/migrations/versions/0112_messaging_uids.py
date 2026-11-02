"""messaging.{conversations,groups,attachments}.uid -- the rows a URL names

Revision ID: 0112_messaging_uids
Revises: 0111_accounts_uid_not_null
Create Date: 2026-08-11

A portal URL never carries a serial id (docs/api.md -> "Addressing"). Messaging shipped
addressing conversations, groups and attachments by `id`; these are the handles that replace
them. Messages get none -- nothing addresses a message.
"""

from pathlib import Path

from alembic import op

revision: str = "0112_messaging_uids"
down_revision: str | None = "0111_accounts_uid_not_null"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0112_messaging_uids.sql")


def downgrade() -> None:
    # The columns go; the uids in them do not need keeping, because nothing outside a URL ever
    # held one (no FK references a uid -- every relation is by id).
    op.get_bind().exec_driver_sql("ALTER TABLE messaging.attachments DROP COLUMN uid")
    op.get_bind().exec_driver_sql("ALTER TABLE messaging.groups DROP COLUMN uid")
    op.get_bind().exec_driver_sql("ALTER TABLE messaging.conversations DROP COLUMN uid")
