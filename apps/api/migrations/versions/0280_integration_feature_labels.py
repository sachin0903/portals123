"""Name the two income-statement features for what they are

Revision ID: 0280_integration_feature_labels
Revises: 0279_drop_integrations_audit
Create Date: 2026-10-21

The Features toggles show ``integrations.features.description`` as the label; 0231 seeded the two
income-statement ones as sentences. See the SQL.

``alembic_version.version_num`` is ``varchar(32)``; this id is 31 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0280_integration_feature_labels"
down_revision: str | None = "0279_drop_integrations_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0280_integration_feature_labels.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Labels only; there is nothing worth restoring the old wording for.
    pass
