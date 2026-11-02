"""notifications.tenant_config -- adviser/client audience split + a mandatory flag

Revision ID: 0070_tenant_config_audience
Revises: 0069_drop_client_can_disable
Create Date: 2026-08-07

The admin defaults matrix splits a firm's per-category defaults into an advisers section and a
clients section, each with a mandatory toggle (docs/notifications.md). Expand phase; DDL in the
sibling SQL file.
"""

from pathlib import Path

from alembic import op

revision: str = "0070_tenant_config_audience"
down_revision: str | None = "0069_drop_client_can_disable"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0070_tenant_config_audience.sql")


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(
        "ALTER TABLE notifications.tenant_config "
        "DROP CONSTRAINT IF EXISTS tenant_config_audience_valid, "
        "DROP CONSTRAINT IF EXISTS tenant_config_key"
    )
    # Collapse to one row per (tenant, category) before restoring the old key.
    bind.exec_driver_sql(
        "DELETE FROM notifications.tenant_config a USING notifications.tenant_config b "
        "WHERE a.tenant_id = b.tenant_id AND a.category = b.category AND a.id > b.id"
    )
    bind.exec_driver_sql(
        "ALTER TABLE notifications.tenant_config "
        "DROP COLUMN IF EXISTS audience, DROP COLUMN IF EXISTS mandatory, "
        "ADD CONSTRAINT tenant_config_tenant_id_category_key UNIQUE (tenant_id, category)"
    )
