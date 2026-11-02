"""One Intelliflo plan is linked to one account, across all tenants

Revision ID: 0278_intelliflo_plan_unique
Revises: 0277_integrations_items_ledger
Create Date: 2026-10-19

A unique partial index on ``platform.links (data ->> 'id')`` for Intelliflo account links, so the
one-plan-per-account rule holds across tenants and under a race. See the SQL.

``alembic_version.version_num`` is ``varchar(32)``; this id is 26 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0278_intelliflo_plan_unique"
down_revision: str | None = "0277_integrations_items_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0278_intelliflo_plan_unique.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("DROP INDEX IF EXISTS platform.links_intelliflo_plan_uq")
    bind.exec_driver_sql(
        "DROP FUNCTION IF EXISTS platform.provider_code_text(platform.provider_code)"
    )
