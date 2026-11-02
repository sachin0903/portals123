"""storage.tenants client read — a client can read their own tenant's storage row

Revision ID: 0082_storage_tenants_client_read
Revises: 0081_messaging_attachments
Create Date: 2026-08-05

tenant_lineage() is deliberately empty for a client-portal principal, so the previous
storage.tenants read policy left a client unable to resolve their own tenant's bucket at all —
blocking chat attachments client-side. Widens the policy with auth.tenant_id() (the raw JWT
claim, populated for clients too). DDL in the sibling SQL file; applied as the elevated migrator
role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0082_storage_tenants_client_read"
down_revision: str | None = "0081_messaging_attachments"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text().replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0082_storage_tenants_client_read.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "DROP POLICY IF EXISTS storage_tenants_read ON storage.tenants;"
        "CREATE POLICY storage_tenants_read ON storage.tenants FOR SELECT TO authenticated "
        "  USING (tenant_id = ANY (auth.tenant_lineage()));"
    )
