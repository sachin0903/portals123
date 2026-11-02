"""integrations.platforms.provider_id/provider_name: re-added with a DB default (stopgap)

Revision ID: 0274_intelliflo_provider_default
Revises: 0273_integrations_tenant
Create Date: 2026-10-12

0273_integrations_tenant dropped these on the reasoning that nothing could set them any more and
neither had ever held a value -- true of the DB and the admin UI, but services/integrations/
intelliflo still reads them on every Intelliflo API call (see this migration's own .sql file for
the detail). Re-added as NOT NULL DEFAULT rather than restoring the old nullable, admin-set shape,
since they were never genuinely per-tenant in the first place. Stopgap pending a move to
provider-level settings -- see the .sql file's own note.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0274_intelliflo_provider_default"
down_revision: str | None = "0273_integrations_tenant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0274_intelliflo_provider_default.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE integrations.platforms DROP COLUMN IF EXISTS provider_id, "
        "DROP COLUMN IF EXISTS provider_name;"
    )
