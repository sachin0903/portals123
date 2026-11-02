"""compliance: a change to a client's screened details marks their clear AML screen stale

Revision ID: 0223_contact_rescreen
Revises: 0222_partner_api_extras
Create Date: 2026-10-01
"""

from pathlib import Path

from alembic import op

revision: str = "0223_contact_rescreen"
down_revision: str | None = "0222_partner_api_extras"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0223_contact_rescreen.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP TRIGGER IF EXISTS mark_screen_stale ON crm.contacts;
        DROP FUNCTION IF EXISTS compliance.mark_screen_stale();
        REVOKE CREATE ON SCHEMA compliance FROM service_role;
        """
    )
