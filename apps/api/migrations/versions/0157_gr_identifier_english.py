"""English localeName for Greece tax identifiers (UAT-8)

Revision ID: 0157_gr_identifier_english
Revises: 0156_jisa_contact_scope
Create Date: 2026-09-13
"""

from pathlib import Path

from alembic import op

revision: str = "0157_gr_identifier_english"
down_revision: str | None = "0156_jisa_contact_scope"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0157_gr_identifier_english.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    pass
