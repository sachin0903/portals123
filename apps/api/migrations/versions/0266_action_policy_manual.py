"""a per-action "manual" switch that halts a run until a person accepts it

Revision ID: 0266_action_policy_manual
Revises: 0265_adviser_fee_type
Create Date: 2026-10-20

Adds actions.policies.manual. When set, the action halts as a manual step (the approval-hold
machinery) and accepting completes the run without a provider call — for actions whose provider
API is not built yet.
"""

from pathlib import Path

from alembic import op

revision: str = "0266_action_policy_manual"
down_revision: str | None = "0265_adviser_fee_type"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0266_action_policy_manual.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql("ALTER TABLE actions.policies DROP COLUMN manual;")
