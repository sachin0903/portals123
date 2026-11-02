"""Registered-contact CRM relationship type

Revision ID: 0168_registered_contact_rel
Revises: 0167_product_bank_details
Create Date: 2026-09-15
"""

from pathlib import Path

from alembic import op

revision: str = "0168_registered_contact_rel"
down_revision: str | None = "0167_product_bank_details"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0168_registered_contact_relationship_type.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The reverse swap, for the same reason the upgrade is one: a member cannot be removed from an
    # enum in place. 0169's downgrade deletes its own rows first (revisions unwind in reverse), so
    # this normally casts cleanly; a `registered_contact` row written since then fails the cast
    # rather than being deleted by a downgrade nobody asked to lose data to.
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TYPE crm.relationship_type RENAME TO relationship_type_v2;
        CREATE TYPE crm.relationship_type AS ENUM (
          'spouse','partner','parent','child','dependant','director','shareholder',
          'trustee','beneficiary','attorney','adviser','other');
        ALTER TABLE crm.relationships
          ALTER COLUMN relationship_type TYPE crm.relationship_type
          USING relationship_type::text::crm.relationship_type;
        DROP TYPE crm.relationship_type_v2;
        """
    )
