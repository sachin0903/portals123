"""One adviser per client; a client's team holds a fixed list of staff roles.

Revision ID: 0245_one_adviser_per_client
Revises: 0244_merge_account_party
Create Date: 2026-10-09

``alembic_version.version_num`` is ``varchar(32)``; this id is 27 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0245_one_adviser_per_client"
down_revision: str | None = "0244_merge_account_party"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0245_one_adviser_per_client.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The re-labelling is not reversed: the free-text values it replaced are gone, and every
    # earlier reader handles `adviser`/`support` (they sorted by name or by `lead`).
    op.get_bind().exec_driver_sql(
        """
        DROP INDEX IF EXISTS crm.client_advisers_one_adviser_key;
        ALTER TABLE crm.client_advisers
          DROP CONSTRAINT IF EXISTS client_advisers_role_known,
          ALTER COLUMN role DROP NOT NULL;
        """
    )
