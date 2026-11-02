"""Add party to crm.account_role (a journey participant with no account).

Revision ID: 0243_account_role_party
Revises: 0242_form_scoring
Create Date: 2026-10-07

``alembic_version.version_num`` is ``varchar(32)``; this id is 23 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0243_account_role_party"
down_revision: str | None = "0242_form_scoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0243_account_role_party.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Postgres cannot DROP an enum value; leaving party in place is harmless.
    pass
