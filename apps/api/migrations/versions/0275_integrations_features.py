"""integrations.features: only the four features the engine runs

Revision ID: 0275_integrations_features
Revises: 0274_intelliflo_provider_default
Create Date: 2026-10-14

0231 seeds the legacy sei_reports feature list; eight of its entries (Analytics, Calendar, Clients,
Deployments, Email, Notifications, Pricing, Reporting) are run by nothing and only add dead toggles
to an integration's Features list. This removes the ones no platform has enabled, leaving
Valuations, Transactions, IncomeStatements and IncomeStatementItems. See the SQL for why it is a
new revision and not an edit to 0231, and for the row-level-security handling.

``alembic_version.version_num`` is ``varchar(32)``; this id is 27 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0275_integrations_features"
down_revision: str | None = "0274_intelliflo_provider_default"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0275_integrations_features.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The removed rows were reference data nothing ran, and 0231 owns re-seeding them. Re-running a
    # downgrade must not resurrect dead toggles, so this is deliberately a no-op.
    pass
