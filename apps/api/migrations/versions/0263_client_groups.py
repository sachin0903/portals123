"""client groups: assigned tags that can select a portal design

Revision ID: 0263_client_groups
Revises: 0262_onboarding_fatca_upload
Create Date: 2026-10-20

``alembic_version.version_num`` is ``varchar(32)``; this id is 18 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0263_client_groups"
down_revision: str | None = "0262_onboarding_fatca_upload"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0263_client_groups.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP TABLE IF EXISTS crm.contact_groups;
        DROP TABLE IF EXISTS crm.client_groups;
        """
    )
