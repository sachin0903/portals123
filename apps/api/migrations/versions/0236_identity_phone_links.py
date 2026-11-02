"""One-time links for taking the identity photos on a phone.

Revision ID: 0236_identity_phone_links
Revises: 0235_identity_photos
Create Date: 2026-10-06

``alembic_version.version_num`` is ``varchar(32)``; this id is 26 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0236_identity_phone_links"
down_revision: str | None = "0235_identity_photos"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0236_identity_phone_links.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP TABLE IF EXISTS auth.identity_phone_links;
        """
    )
