"""fold the integrations schema into platform

Revision ID: 0048_providers_into_platform
Revises: 0047_api_log
Create Date: 2026-08-02

The DDL lives in the sibling SQL file -- that file is the reviewable artifact; this script just
applies it. Runs as the elevated migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0048_providers_into_platform"
down_revision: str | None = "0047_api_log"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0048_providers_into_platform.sql")


def downgrade() -> None:
    # A pure rename reverses exactly. Recreate the schema first, or SET SCHEMA has nowhere to
    # put the tables back.
    op.execute("CREATE SCHEMA IF NOT EXISTS integrations")
    op.execute("GRANT USAGE ON SCHEMA integrations TO anon, authenticated, service_role")
    op.execute(
        "ALTER TABLE platform.providers RENAME CONSTRAINT providers_provider_external_key "
        "TO provider_sources_provider_source_key"
    )
    op.execute("ALTER TABLE platform.providers RENAME COLUMN external_id TO source_id")
    op.execute("ALTER TABLE platform.providers RENAME TO provider_sources")
    op.execute("ALTER TABLE platform.provider_sources SET SCHEMA integrations")
    op.execute(
        "ALTER POLICY tenant_platforms_read ON platform.tenant_platforms "
        "RENAME TO tenant_providers_read"
    )
    op.execute(
        "ALTER INDEX platform.tenant_platforms_tenant_id_idx "
        "RENAME TO tenant_providers_tenant_id_idx"
    )
    op.execute(
        "ALTER TABLE platform.tenant_platforms RENAME CONSTRAINT "
        "tenant_platforms_tenant_provider_key TO tenant_providers_tenant_provider_key"
    )
    op.execute("ALTER TABLE platform.tenant_platforms RENAME TO tenant_providers")
    op.execute("ALTER TABLE platform.tenant_providers SET SCHEMA integrations")
