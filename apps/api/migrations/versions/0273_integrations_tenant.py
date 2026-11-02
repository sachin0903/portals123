"""integrations.platforms.firm_id: SEI firm code -> auth.tenants(id); drop dsr_codes/provider_*

Revision ID: 0273_integrations_tenant
Revises: 0272_intelliflo_provider
Create Date: 2026-10-09

Applied to dev on 9 Oct as `0246_integrations_tenant`, off `0245_intelliflo_provider` directly --
main independently landed its own `0245`/`0246` off `0244_merge_account_party` while this branch
was unmerged, colliding on that exact id (see `0246_integrations_tenant`'s own stub and
`0247_merge_integrations`, which join main's two branches but know nothing of this one). Refiled
here as a linear tail off main's current head instead of off the old stamped ids, same as
`0272_intelliflo_provider` above it -- a database already on dev's stamped id reaches this point
through the stub/merge, so its own `upgrade()` had to be made safe to run twice (the DDL already
exists there from the original, uncommitted run); a fresh database runs it for the first time.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0273_integrations_tenant"
down_revision: str | None = "0272_intelliflo_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0273_integrations_tenant.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE integrations.platforms "
        "  ADD COLUMN IF NOT EXISTS dsr_codes text[] NOT NULL DEFAULT '{}', "
        "  ADD COLUMN IF NOT EXISTS provider_id text, "
        "  ADD COLUMN IF NOT EXISTS provider_name text; "
        "ALTER TABLE integrations.platforms DROP CONSTRAINT IF EXISTS platforms_firm_id_fkey; "
        "ALTER TABLE integrations.platforms "
        "  DROP CONSTRAINT IF EXISTS platforms_firm_id_platform_name_tenant_id_key; "
        "ALTER TABLE integrations.platforms ALTER COLUMN firm_id TYPE text USING NULL; "
        "DO $$ BEGIN "
        "  ALTER TABLE integrations.platforms "
        "    ADD CONSTRAINT platforms_firm_id_fkey "
        "    FOREIGN KEY (firm_id) REFERENCES integrations.firms(code) ON DELETE SET NULL; "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$; "
        "DO $$ BEGIN "
        "  ALTER TABLE integrations.platforms "
        "    ADD CONSTRAINT platforms_firm_id_platform_name_tenant_id_key "
        "    UNIQUE (firm_id, platform_name, tenant_id); "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
