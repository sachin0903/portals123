"""generic config-driven flow engine (flows schema) + shares flow_case widen

Revision ID: 0021_flows
Revises: 0020_dataset_format_liveness
Create Date: 2026-07-22

The flows.* schema (flows / flow_versions / cases / case_steps): firm-authored, versioned process
definitions and their running instances, on which onboarding is the first published flow. Also
widens documents.shares.resource_type to include 'flow_case'. DDL in the sibling SQL file; applied
as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021_flows"
down_revision: str | None = "0020_dataset_format_liveness"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_DOWN = """
DROP SCHEMA IF EXISTS flows CASCADE;
ALTER TABLE documents.shares DROP CONSTRAINT IF EXISTS shares_resource_type_check;
ALTER TABLE documents.shares ADD CONSTRAINT shares_resource_type_check
  CHECK (resource_type IN ('document', 'file'));
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0021_flows.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
