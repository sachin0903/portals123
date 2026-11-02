"""platform.accounts.uid NOT NULL -- an account with no uid has no address

Revision ID: 0111_accounts_uid_not_null
Revises: 0110_nanoid
Create Date: 2026-08-10

crm.contacts.uid has been NOT NULL since 0002; platform.accounts.uid was merely UNIQUE, and the
asymmetry was never deliberate. Every writer sets it. Now that the portals address an account by
its uid, a NULL would be an account no URL can reach.
"""

from pathlib import Path

from alembic import op

revision: str = "0111_accounts_uid_not_null"
down_revision: str | None = "0110_nanoid"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0111_accounts_uid_not_null.sql")


def downgrade() -> None:
    # The backfilled uids stay: they are real identifiers now, and dropping them would break
    # any URL already issued. Only the constraint is lifted.
    op.get_bind().exec_driver_sql("ALTER TABLE platform.accounts ALTER COLUMN uid DROP NOT NULL")
