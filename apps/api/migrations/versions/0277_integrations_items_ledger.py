"""integrations.income_statement_items_sent: add the attempt marker

Revision ID: 0277_integrations_items_ledger
Revises: 0276_integrations_txn_ledger
Create Date: 2026-10-16

Adds ``status`` (``attempted`` / ``confirmed`` / ``held``), ``attempted_at``, ``batch_id`` and
``fingerprint`` so an income-items batch whose outcome is unknown is reconciled against Intelliflo
instead of being re-sent (IO accepts exact duplicates and has no delete). See the SQL for why.

``alembic_version.version_num`` is ``varchar(32)``; this id is 29 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0277_integrations_items_ledger"
down_revision: str | None = "0276_integrations_txn_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0277_integrations_items_ledger.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Additive columns on a ledger; dropping them would discard the record of what was sent.
    pass
