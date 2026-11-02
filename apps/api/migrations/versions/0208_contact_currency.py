"""a client's base currency, beside the account's

Revision ID: 0208_contact_currency
Revises: 0207_terms_acceptance_evidence
Create Date: 2026-09-29

``alembic_version.version_num`` is ``varchar(32)``; this id is 21 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0208_contact_currency"
down_revision: str | None = "0207_terms_acceptance_evidence"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0208_contact_currency.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS contacts_currency_known ON crm.contacts")
    op.execute("DROP FUNCTION IF EXISTS crm.coerce_unknown_contact_currency()")
    op.execute("REVOKE SELECT ON ref.currencies FROM book_writer")
    op.execute("REVOKE USAGE ON SCHEMA ref FROM book_writer")
    op.execute("ALTER TABLE crm.contacts DROP COLUMN IF EXISTS currency")
