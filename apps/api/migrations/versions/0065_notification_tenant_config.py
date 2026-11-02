"""notification tenant config -- the firm-level layer of preference resolution (admin-editable)

Revision ID: 0065_notification_tenant_config
Revises: 0064_notification_dismiss
Create Date: 2026-08-06

Adds notifications.tenant_config: per (tenant, category) an `enabled` firm-wide off-switch + channel
defaults, sitting between the registry defaults and each user's overrides. Written by the admin
path (service_role), read by the subscribers and (own firm only) by staff. Expand only; DDL in the
sibling SQL file.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0065_notification_tenant_config"
down_revision: str | None = "0064_notification_dismiss"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0065_notification_tenant_config.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS notifications.tenant_config")
