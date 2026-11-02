"""add registered_contact to crm.account_role (JISA adult)

Revision ID: 0155_registered_contact
Revises: 0154_instruction_adhoc_fee
Create Date: 2026-09-13
"""

from pathlib import Path

from alembic import op

revision: str = "0155_registered_contact"
down_revision: str | None = "0154_instruction_adhoc_fee"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0155_account_role_registered_contact.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Enum members are not removed — a JISA row may already hold registered_contact.
    pass
