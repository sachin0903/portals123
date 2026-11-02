"""One primary mobile plus a list of the client's other numbers

Revision ID: 0165_contact_phone_numbers
Revises: 0164_tenant_data_tools
Create Date: 2026-09-15
"""

from pathlib import Path

from alembic import op

revision: str = "0165_contact_phone_numbers"
down_revision: str | None = "0164_tenant_data_tools"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0164_contact_phone_numbers.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The column comes back and the landline entries go back into it, first entry wins. This is
    # lossy by construction — a client who gained a second and third number while the array
    # existed keeps only one on the way down, and a number promoted to `mobile_phone` by the
    # upgrade stays there. Down is for unblocking a bad deploy, not for round-tripping data.
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE crm.contacts
          ADD COLUMN IF NOT EXISTS phone text;

        SET LOCAL ROLE service_role;
        UPDATE crm.contacts
           SET phone = phone_numbers -> 0 ->> 'number'
         WHERE jsonb_typeof(phone_numbers) = 'array'
           AND jsonb_array_length(phone_numbers) > 0;
        RESET ROLE;

        ALTER TABLE crm.contacts
          DROP CONSTRAINT IF EXISTS contacts_phone_numbers_is_array,
          DROP COLUMN IF EXISTS phone_numbers;
        """
    )
