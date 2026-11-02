"""`intelliflo` joins platform.provider_code, for the "Link to IO" card's platform.links rows

Revision ID: 0272_intelliflo_provider
Revises: 0271_employer_bank_account
Create Date: 2026-10-01

Applied to dev on 9 Oct as `0245_intelliflo_provider`, off `0244_merge_account_party` directly --
main independently landed its own `0245`/`0246` off the same parent while this branch was
unmerged, which collided on `0246_integrations_tenant` (see that id's own stub and
`0247_merge_integrations`). Refiled here as a linear tail off main's current head, same as
`0273_integrations_tenant` below it: a database already on dev's stamped id reaches this point
through the stub/merge instead, and `ALTER TYPE ... ADD VALUE IF NOT EXISTS` already made the DDL
itself safe to run twice, so the rename needed no change to the SQL.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0272_intelliflo_provider"
down_revision: str | None = "0271_employer_bank_account"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0272_intelliflo_provider.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Enum values cannot be dropped (0143/0197's own rule: members are added, never removed).
    pass
