"""repair the access-kind and membership-role backfill 0119 skipped

Revision ID: 0120_access_repair
Revises: 0119_access_profiles_roles
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0120_access_repair"
down_revision: str | None = "0119_access_profiles_roles"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0120_access_repair.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    """Deliberately a no-op.

    There is nothing here worth undoing on its own. The backfilled `auth.membership_roles` rows ARE
    the permissions every session reads, so dropping them is the outage this migration repairs;
    and restoring 0119's stricter trigger definitions buys nothing, because the only way past this
    revision is 0119's own downgrade, which drops those functions and the table outright."""
