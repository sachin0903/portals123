"""Drop the audit triggers from the integrations tables

Revision ID: 0279_drop_integrations_audit
Revises: 0278_intelliflo_plan_unique
Create Date: 2026-10-21

0231 attached ``audit_row`` to integrations.platforms, platform_features and advisors_map; their
``tenant_id`` is a vendor id, so the audit insert fails on audit.log's tenant foreign key. See the
SQL.

``alembic_version.version_num`` is ``varchar(32)``; this id is 27 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0279_drop_integrations_audit"
down_revision: str | None = "0278_intelliflo_plan_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0279_drop_integrations_audit.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Re-attaching would restore a trigger that fails on these tables; nothing to put back.
    pass
