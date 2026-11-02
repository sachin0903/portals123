"""the employer's bank account gets its own column on the client record

Revision ID: 0271_employer_bank_account
Revises: 0270_retire_book_reviews
Create Date: 2026-10-22

The onboarding fact-find asks for the employer's sort code and account number (the scheme paying
in) and had nowhere to put them: 0210 moved every other employer answer onto the Employer card's
columns and left those two in ``crm.contacts.data.pension.employerContributions``, where the
record showed them as "2 more from the fact-find" — two bare digits strings under the wrong
heading, with no editor and no directory check. ``employer_bank_account`` is the same jsonb shape
as ``bank_account`` (``{name, bank, sort_code, account_number}``), written through the same
normaliser and the same sort-code directory, shown and edited on the Employer card.

Expand only: the column is added and the rows written the old way are backfilled, with the two
keys removed from ``data`` so the record does not state each fact twice.
"""

from pathlib import Path

from alembic import op

revision: str = "0271_employer_bank_account"
down_revision: str | None = "0270_retire_book_reviews"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0271_employer_bank_account.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE crm.contacts DROP COLUMN IF EXISTS employer_bank_account"
    )
