"""portal hosts unique across environments (close the duplicate-host cross-tenant binding)

Revision ID: 0099_portal_host_unique
Revises: 0094_portal_from_application
Create Date: 2026-08-16

Adds partial unique indexes on auth.tenant_environments.client_portal_host / platform_host so two
environments can no longer claim the same portal host (auth review finding #1). DDL in the sibling
SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0099_portal_host_unique"
down_revision: str | None = "0098_portal_from_application"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0099_portal_host_unique.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP INDEX IF EXISTS auth.tenant_environments_client_portal_host_key; "
        "DROP INDEX IF EXISTS auth.tenant_environments_platform_host_key;"
    )
