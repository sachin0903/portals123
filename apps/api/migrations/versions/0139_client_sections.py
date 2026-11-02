"""crm: the restructured client record's fields, and beneficiary nominations

Revision ID: 0139_client_sections
Revises: 0138_contact_employment
Create Date: 2026-09-08
"""

from pathlib import Path

from alembic import op

revision: str = "0139_client_sections"
down_revision: str | None = "0138_contact_employment"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    name = "0139_client_sections.sql"
    sql = (_SQL_DIR / name).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE crm.contact_notes DROP COLUMN IF EXISTS uid;
        DROP TABLE IF EXISTS crm.beneficiaries;
        ALTER TABLE crm.contacts
          DROP COLUMN IF EXISTS employer_contributions,
          DROP COLUMN IF EXISTS employer_registration_number,
          DROP COLUMN IF EXISTS employer_address,
          DROP COLUMN IF EXISTS flexi_access_date,
          DROP COLUMN IF EXISTS source_of_wealth_detail;
        """
    )
