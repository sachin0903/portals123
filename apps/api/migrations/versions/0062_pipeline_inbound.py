"""pipeline: the inbound landing zone and the merge reject ledger

Revision ID: 0062_pipeline_inbound
Revises: 0061_tenant_service_users
Create Date: 2026-08-05

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0062_pipeline_inbound"
down_revision: str | None = "0061_tenant_service_users"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0062_pipeline_inbound.sql")


def downgrade() -> None:
    # `inbound` is dropped with CASCADE because its tables are interlace's, not ours — they are
    # rebuilt by the next run. The reject ledger goes too: it is a record of runs that no longer
    # have a mechanism, and keeping it would leave rows nothing can explain.
    op.execute(
        """
        SET LOCAL ROLE service_role;
        DROP TABLE IF EXISTS platform.merge_rejects;
        DROP SCHEMA IF EXISTS inbound CASCADE;
        RESET ROLE;
        """
    )
