"""a firm's verified Resend sending domain

Revision ID: 0264_sender_domains
Revises: 0263_client_groups
Create Date: 2026-10-20

``alembic_version.version_num`` is ``varchar(32)``; this id is 19 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0264_sender_domains"
down_revision: str | None = "0263_client_groups"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0264_sender_domains.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP TABLE IF EXISTS email.sender_domains;
        """
    )
