"""flows.automations — the automation plane (armed triggers, cursor, deliveries, kill switch)

Revision ID: 0043_automations
Revises: 0042_permission_splits
Create Date: 2026-08-03

Flows v3 Phase A: one ordered table of armed automations per tenant (a row either starts a flow
or performs one action), the durable consumer position over ``audit.actions``, the delivery
records that make at-least-once redelivery safe, and the per-tenant pause/override row. Expand
only. DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0043_automations"
down_revision: str | None = "0042_permission_splits"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0043_automations.sql")


def downgrade() -> None:
    # Drop in dependency order; the initiation CHECK narrows back, which is only safe because a
    # downgrade implies no automation ever ran (its cases would be going too).
    op.get_bind().exec_driver_sql(
        "DROP TABLE IF EXISTS flows.automation_deliveries; "
        "DROP TABLE IF EXISTS flows.automation_cursor; "
        "DROP TABLE IF EXISTS flows.automation_settings; "
        "DROP TABLE IF EXISTS flows.automations; "
        "DELETE FROM flows.cases WHERE initiation = 'automation'; "
        "ALTER TABLE flows.cases DROP CONSTRAINT IF EXISTS cases_initiation_check; "
        "ALTER TABLE flows.cases ADD CONSTRAINT cases_initiation_check "
        "  CHECK (initiation IN ('adviser', 'client')); "
        "DELETE FROM tasks.tasks WHERE source_kind = 'automation'; "
        "ALTER TABLE tasks.tasks DROP CONSTRAINT IF EXISTS tasks_source_kind_check; "
        "ALTER TABLE tasks.tasks ADD CONSTRAINT tasks_source_kind_check "
        "  CHECK (source_kind IN ('manual', 'flow', 'document', 'system'))"
    )
