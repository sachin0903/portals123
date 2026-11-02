"""drop notifications.rules.client_can_disable -- a dead guardrail beside `mandatory`

Revision ID: 0069_drop_client_can_disable
Revises: 0068_notification_rules
Create Date: 2026-08-07

Nothing in resolution read client_can_disable; only `mandatory` forces a recipient's default. Two
switches for one idea, so the redundant one is dropped (docs/notifications.md). Contract phase.
"""

from pathlib import Path

from alembic import op

revision: str = "0069_drop_client_can_disable"
down_revision: str | None = "0068_notification_rules"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0069_drop_client_can_disable.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE notifications.rules "
        "ADD COLUMN IF NOT EXISTS client_can_disable boolean NOT NULL DEFAULT true"
    )
