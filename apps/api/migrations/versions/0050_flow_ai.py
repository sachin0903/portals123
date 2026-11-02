"""flow AI gates — the per-tenant switch (flows.ai_tenants) and the token meter (flows.ai_usage)

Revision ID: 0050_flow_ai
Revises: 0049_delivery_settled_windows
Create Date: 2026-08-04

Phase D2 adds two action types that can reach a language model (`classify`, `draft_message`).
Both are off unless a deployment carries `ANTHROPIC_API_KEY` **and** the firm has a row here
saying it may — two switches, because a configured deployment must not mean an enabled firm.
`flows.ai_usage` is the meter the per-tenant monthly token cap is enforced against, and it
deliberately holds no prompt and no completion. DDL in the sibling SQL file; applied as the
elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0050_flow_ai"
down_revision: str | None = "0049_delivery_settled_windows"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0050_flow_ai.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s';"
        "DROP TABLE IF EXISTS flows.ai_usage;"
        "DROP TABLE IF EXISTS flows.ai_tenants;"
    )
