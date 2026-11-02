"""crm.contacts: occupation, place of birth, source of funds, tax relief, vulnerability chars

Revision ID: 0129_contact_kyc_facts
Revises: 0128_messaging_support_sender
Create Date: 2026-09-04
"""

from pathlib import Path

from alembic import op

revision: str = "0129_contact_kyc_facts"
down_revision: str | None = "0128_messaging_support_sender"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0129_contact_kyc_facts.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE crm.contacts
          DROP CONSTRAINT IF EXISTS contacts_vulnerability_characteristics_coherent;
        ALTER TABLE crm.contacts
          DROP COLUMN IF EXISTS occupation,
          DROP COLUMN IF EXISTS occupation_detail,
          DROP COLUMN IF EXISTS country_of_birth,
          DROP COLUMN IF EXISTS town_of_birth,
          DROP COLUMN IF EXISTS source_of_funds,
          DROP COLUMN IF EXISTS source_of_funds_detail,
          DROP COLUMN IF EXISTS tax_relief,
          DROP COLUMN IF EXISTS vulnerability_characteristics;
        """
    )
