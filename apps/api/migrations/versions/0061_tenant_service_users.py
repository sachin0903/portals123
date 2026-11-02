"""storage: four scoped service users per tenant bucket

Revision ID: 0061_tenant_service_users
Revises: 0060_merge_flows_and_platform
Create Date: 2026-08-04

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0061_tenant_service_users"
down_revision: str | None = "0060_merge_flows_and_platform"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0061_tenant_service_users.sql")


def downgrade() -> None:
    # The base credential still lives in storage.tenants/tenant_secrets (this migration
    # copied it rather than moving it), so dropping these tables loses only the three
    # narrower principals. They exist in the object store as well; a re-provision after a
    # downgrade re-adopts base and re-mints the rest.
    op.execute(
        """
        SET LOCAL ROLE service_role;
        DROP TABLE IF EXISTS storage.tenant_service_user_secrets;
        DROP TABLE IF EXISTS storage.tenant_service_users;
        RESET ROLE;
        """
    )
