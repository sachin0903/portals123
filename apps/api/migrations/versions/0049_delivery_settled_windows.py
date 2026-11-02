"""delivery settled windows — the automation throttles count when a firing RAN, not when it arrived

Revision ID: 0049_delivery_settled_windows
Revises: 0048_automation_platform_pause
Create Date: 2026-08-04

`flows.automation_deliveries` rows are re-settled in place when a deferred firing finally runs, so
`created_at` is the arrival time, not the dispatch time — and both throttles windowed on it, which
let every drained deferral escape the per-tenant rate limit and the per-subject re-arm. The queries
now read `updated_at`; this swaps the two partial indexes to match. DDL in the sibling SQL file;
applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0049_delivery_settled_windows"
down_revision: str | None = "0048_automation_platform_pause"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0049_delivery_settled_windows.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "CREATE INDEX IF NOT EXISTS automation_deliveries_rearm_idx "
        "  ON flows.automation_deliveries "
        "     (automation_id, subject_kind, subject_id, created_at DESC) "
        "  WHERE outcome = 'dispatched';"
        "CREATE INDEX IF NOT EXISTS automation_deliveries_rate_idx "
        "  ON flows.automation_deliveries (tenant_id, created_at DESC) "
        "  WHERE outcome = 'dispatched';"
        "DROP INDEX IF EXISTS flows.automation_deliveries_rearm_settled_idx;"
        "DROP INDEX IF EXISTS flows.automation_deliveries_rate_settled_idx;"
    )
