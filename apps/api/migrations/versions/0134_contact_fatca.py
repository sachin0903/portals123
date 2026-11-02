"""crm.contacts: the FATCA self-certification and the passport

Revision ID: 0134_contact_fatca
Revises: 0133_account_money_facts
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0134_contact_fatca"
down_revision: str | None = "0133_account_money_facts"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0134_contact_fatca.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE crm.contacts DROP CONSTRAINT IF EXISTS contacts_fatca_coherent, DROP CONSTRAINT IF EXISTS contacts_fatca_status_check, DROP COLUMN IF EXISTS us_person, DROP COLUMN IF EXISTS fatca_status, DROP COLUMN IF EXISTS fatca_certified_at, DROP COLUMN IF EXISTS passport_number, DROP COLUMN IF EXISTS passport_country;
        """
    )
