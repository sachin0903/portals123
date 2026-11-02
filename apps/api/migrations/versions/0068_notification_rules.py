"""notification rules -- firm-configurable notification rules (the authorable builder)

Revision ID: 0068_notification_rules
Revises: 0067_notification_user_scope
Create Date: 2026-08-06

The foundation for the authorable notification-rule builder (docs/notifications.md → "Planned —
authorable notification rules"): notifications.rules, one authored notification per row (trigger →
audience → copy + guardrails). Scoping mirrors documents (staff by lineage; a client reads its own
tenant's client-audience rules); writes are service_role, app-layer permission gated. Expand only;
DDL in the sibling SQL file. The consumer + API + UI that use this land in later phases.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0068_notification_rules"
down_revision: str | None = "0067_notification_user_scope"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0068_notification_rules.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS notifications.rules")
