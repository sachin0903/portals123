"""automation platform pause — the estate-wide kill switch behind automations:global_pause

Revision ID: 0048_automation_platform_pause
Revises: 0047_flow_analytics
Create Date: 2026-08-04

One singleton table (`flows.automation_platform_pause`), service_role-only, audited. It gives
`automations:global_pause` — registered since 0043 and enforced nowhere — something to enforce.
DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0048_automation_platform_pause"
down_revision: str | None = "0047_flow_analytics"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0048_automation_platform_pause.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS flows.automation_platform_pause")
