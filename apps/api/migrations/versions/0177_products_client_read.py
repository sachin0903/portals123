"""products_read: a client can see their own firm's shelf

Revision ID: 0177_products_client_read
Revises: 0176_transfer_pension_facts
Create Date: 2026-09-17

tenant_lineage() is empty in the client portal, so the 0042 products policy hid every
firm-scoped wrapper type and /crm/home labelled every card "Account". Widens with
auth.tenant_id(), the same lever 0082 used for storage.tenants.
"""

from pathlib import Path

from alembic import op

revision: str = "0177_products_client_read"
down_revision: str | None = "0176_transfer_pension_facts"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0177_products_client_read.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "DROP POLICY IF EXISTS products_read ON platform.products;"
        "CREATE POLICY products_read ON platform.products FOR SELECT TO authenticated "
        "  USING (tenant_id IS NULL OR tenant_id = ANY (auth.tenant_lineage()));"
    )
