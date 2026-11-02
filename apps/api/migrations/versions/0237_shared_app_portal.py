"""Classify a shared WorkOS application by the membership the login holds.

Revision ID: 0237_shared_app_portal
Revises: 0236_identity_phone_links
Create Date: 2026-10-06

``alembic_version.version_num`` is ``varchar(32)``; this id is 22 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0237_shared_app_portal"
down_revision: str | None = "0236_identity_phone_links"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0237_shared_app_portal.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    sql = (
        (_SQL_DIR / "0156_jisa_registered_contact_scope.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)
