"""Tenant base and allowed currencies

Revision ID: 0170_tenant_currencies
Revises: 0169_registered_contact_links
Create Date: 2026-09-15
"""

from pathlib import Path

from alembic import op

revision: str = "0170_tenant_currencies"
down_revision: str | None = "0169_registered_contact_links"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0170_tenant_currencies.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP TRIGGER IF EXISTS tenants_validate_currencies ON auth.tenants;
        DROP FUNCTION IF EXISTS auth.validate_tenant_currencies();
        ALTER TABLE auth.tenants
          DROP CONSTRAINT IF EXISTS tenants_allowed_currencies_no_null,
          DROP CONSTRAINT IF EXISTS tenants_base_currency_allowed,
          DROP CONSTRAINT IF EXISTS tenants_allowed_currencies_nonempty,
          DROP COLUMN IF EXISTS allowed_currencies,
          DROP COLUMN IF EXISTS base_currency;
        """
    )
