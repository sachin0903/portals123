"""integrations.transactions_sent: create it where missing, and add the attempt marker

Revision ID: 0276_integrations_txn_ledger
Revises: 0275_integrations_features
Create Date: 2026-10-15

The ledger of transactions posted to Intelliflo existed only on a database that had run an earlier
local migration, so a fresh one had no table and the Transactions feature was blocked. This creates it
where it is missing and adds ``status`` (``attempted`` / ``confirmed``) and ``attempted_at``, which let
the sync tell a batch whose outcome is unknown (a 500 after the rows were stored, a timeout, a crash)
from one that was confirmed. See the SQL for why.

``alembic_version.version_num`` is ``varchar(32)``; this id is 28 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0276_integrations_txn_ledger"
down_revision: str | None = "0275_integrations_features"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0276_integrations_txn_ledger.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The table may predate this revision (the dev database had it first), so dropping it would
    # destroy a ledger this revision did not create. Leaving it, and its columns, is harmless.
    pass
