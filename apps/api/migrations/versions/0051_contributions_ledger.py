"""contributions ledger: expectations + contributions + transfers

Revision ID: 0051_contributions_ledger
Revises: 0050_ref_data
Create Date: 2026-08-03

The DDL lives in the sibling SQL file (migrations/sql/0051_contributions_ledger.sql) -- that
file is the reviewable artifact; this script just applies it. Runs as the elevated
migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0051_contributions_ledger"
down_revision: str | None = "0050_ref_data"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    # exec_driver_sql sends the literal SQL straight to psycopg (no SQLAlchemy bind-param
    # parsing, so `::cast` is safe) as a single multi-statement script. psycopg uses
    # pyformat, so it still scans for `%` placeholders -- double every `%` (in RAISE/
    # format() bodies) so psycopg collapses `%%` back to a literal `%` for the server.
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0051_contributions_ledger.sql")


def downgrade() -> None:
    # Order matters: contributions/transfers reference expectations, and transactions carries the
    # FK this migration added -- drop that constraint before the table it points at.
    op.execute("DROP TABLE IF EXISTS platform.contributions CASCADE")
    op.execute("DROP TABLE IF EXISTS platform.transfers CASCADE")
    op.execute(
        "ALTER TABLE platform.transactions "
        "DROP CONSTRAINT IF EXISTS transactions_expectation_id_fkey"
    )
    op.execute("DROP INDEX IF EXISTS platform.transactions_expectation_idx")
    op.execute("DROP TABLE IF EXISTS platform.transaction_expectations CASCADE")
    # The expectation_id column itself predates this migration (0002) -- leave it.
    op.execute(
        "ALTER TABLE crm.contacts "
        "DROP COLUMN IF EXISTS relevant_uk_earnings, "
        "DROP COLUMN IF EXISTS relevant_uk_earnings_tax_year, "
        "DROP COLUMN IF EXISTS pension_protection"
    )
