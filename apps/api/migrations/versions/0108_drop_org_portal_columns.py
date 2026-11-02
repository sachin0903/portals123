"""drop the org-per-portal schema: per-portal org columns, workos_organisation_id, org helpers

Revision ID: 0108_drop_org_portal_columns
Revises: 0105_app_only_portal
Create Date: 2026-08-19

0105 made the portal app-only; nothing reads these any more. Asserts every live login carries its
tenant before dropping the last fallback that could have resolved one. DDL in the sibling SQL file.
Irreversible by design (the org model is gone); downgrade recreates the empty columns only.
"""

from pathlib import Path

from alembic import op

revision: str = "0108_drop_org_portal_columns"
down_revision: str | None = "0107_app_only_portal"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = ((_SQL_DIR / "0108_drop_org_portal_columns.sql").read_text(encoding="utf-8")).replace(
        "%", "%%"
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Structure only — the org ids themselves are unrecoverable (and unwanted).
    op.get_bind().exec_driver_sql(
        """
        ALTER TABLE auth.tenants ADD COLUMN IF NOT EXISTS workos_organisation_id text UNIQUE;
        ALTER TABLE auth.tenant_environments
          ADD COLUMN IF NOT EXISTS client_portal_org_id text,
          ADD COLUMN IF NOT EXISTS platform_org_id text,
          ADD COLUMN IF NOT EXISTS operations_org_id text;
        CREATE OR REPLACE FUNCTION auth.workos_org() RETURNS text LANGUAGE sql STABLE AS $$
          SELECT NULLIF(auth.jwt() ->> 'org_id', '') $$;
        """
    )
