"""Drop the audit trigger from every table in the integrations schema

Revision ID: 0281_drop_integrations_triggers
Revises: 0280_integration_feature_labels
Create Date: 2026-10-22

0231 attached ``audit_row`` to the integrations tables; their ``tenant_id`` is a vendor id, so the
audit insert fails audit.log's tenant foreign key and the tenant sync's upsert fails with it.
0279 drops three by name; this drops whatever is there. See the SQL.

``alembic_version.version_num`` is ``varchar(32)``; this id is 31 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0281_drop_integrations_triggers"
down_revision: str | None = "0280_integration_feature_labels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0281_drop_integrations_triggers.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Re-attaching would restore a trigger that fails on these tables; nothing to put back.
    pass
