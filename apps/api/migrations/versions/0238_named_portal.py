"""Let a request name its portal when one WorkOS application is shared.

Revision ID: 0238_named_portal
Revises: 0237_shared_app_portal
Create Date: 2026-10-06

``alembic_version.version_num`` is ``varchar(32)``; this id is 17 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0238_named_portal"
down_revision: str | None = "0237_shared_app_portal"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0238_named_portal.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    sql = (_SQL_DIR / "0237_shared_app_portal.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)
