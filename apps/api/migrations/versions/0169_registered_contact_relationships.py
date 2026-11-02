"""Backfill registered-contact CRM relationships

Revision ID: 0169_registered_contact_links
Revises: 0168_registered_contact_rel
Create Date: 2026-09-15
"""

from pathlib import Path

from alembic import op

revision: str = "0169_registered_contact_links"
down_revision: str | None = "0168_registered_contact_rel"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0169_registered_contact_relationships.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL ROLE service_role;
        DELETE FROM crm.relationships WHERE relationship_type = 'registered_contact';
        RESET ROLE;
        """
    )
