"""flow analytics — the cohort indexes behind the step funnel

Revision ID: 0047_flow_analytics
Revises: 0046_case_memory
Create Date: 2026-08-04

Flows v3 Phase C: two indexes for `GET /flows/{uid}/insights`, which aggregates one flow VERSION's
cases + case_steps over a date range. Index-only — no table, no column, no policy — so this is
expand-only and its downgrade costs nothing but query time. DDL in the sibling SQL file; applied
as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0047_flow_analytics"
down_revision: str | None = "0046_case_memory"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0047_flow_analytics.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP INDEX IF EXISTS flows.cases_tenant_version_created_idx; "
        "DROP INDEX IF EXISTS flows.case_steps_funnel_idx"
    )
