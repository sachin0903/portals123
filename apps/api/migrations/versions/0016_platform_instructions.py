"""platform.instructions — demo-grade money-movement instructions (expected-in, withdrawal, model)

Revision ID: 0016_platform_instructions
Revises: 0015_documents_crm_contacts
Create Date: 2026-07-15

A lightweight, audited record of adviser-initiated money movements for the transactions demo
(no provider ledger behind it). Tenant + account-scoped RLS. DDL in the sibling SQL file.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_platform_instructions"
down_revision: str | None = "0015_documents_crm_contacts"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0016_platform_instructions.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.instructions")
