"""crm.contacts.gender -- the field Morgan Lloyd requires and we never held

Revision ID: 0052_contact_gender
Revises: 0051_contributions_ledger
Create Date: 2026-08-03

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0052_contact_gender"
down_revision: str | None = "0051_contributions_ledger"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0052_contact_gender.sql")


def downgrade() -> None:
    op.execute("ALTER TABLE crm.contacts DROP COLUMN IF EXISTS gender")
