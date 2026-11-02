"""crm.contact_notes: the audit trigger its own delete path already claimed

Revision ID: 0147_contact_notes_audit
Revises: 0146_orgs_are_nodes
Create Date: 2026-09-09
"""

from pathlib import Path

from alembic import op

revision: str = "0147_contact_notes_audit"
down_revision: str | None = "0146_orgs_are_nodes"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0147_contact_notes_audit.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP TRIGGER IF EXISTS contact_notes_audit ON crm.contact_notes;"
    )
