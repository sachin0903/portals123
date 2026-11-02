"""crm.contacts: the pension and identity facts our integrations already ask for

Revision ID: 0053_contact_pension_facts
Revises: 0052_contact_gender
Create Date: 2026-08-03

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0053_contact_pension_facts"
down_revision: str | None = "0052_contact_gender"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0053_contact_pension_facts.sql")


def downgrade() -> None:
    for column in (
        "marital_status",
        "retirement_age",
        "employment_status",
        "mobile_phone",
        "source_of_wealth",
        "flexi_access_taken",
        "is_vulnerable",
        "vulnerability_notes",
    ):
        op.execute(f"ALTER TABLE crm.contacts DROP COLUMN IF EXISTS {column}")
