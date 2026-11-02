"""crm.contacts: the client's employer, as a fact about the person

Revision ID: 0138_contact_employment
Revises: 0137_folded_email_and_adviser
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0138_contact_employment"
down_revision: str | None = "0137_folded_email_and_adviser"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    name = "0138_contact_employment.sql"
    sql = (_SQL_DIR / name).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql("ALTER TABLE crm.contacts DROP COLUMN IF EXISTS employer_name;")
