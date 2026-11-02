"""app-only portal classification: the org fallback, the client belt and has_role are gone

Revision ID: 0107_app_only_portal
Revises: 0104_service_type_discretionary
Create Date: 2026-08-19

A session's portal is the WorkOS application it signed in through, held only with an ACTIVE
organisation_memberships row for it. No org fallback, no 'platform' default, no role='client' belt.
tenant_lineage()/can_read_all() become portal whitelists. DDL in the sibling SQL file.
"""

from pathlib import Path

from alembic import op

revision: str = "0107_app_only_portal"
down_revision: str | None = "0105_document_subject_links"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0107_app_only_portal.sql")


def downgrade() -> None:
    # Restore the app-preferred / org-fallback world (0101 resolve_claims, 0030 lineage, 0004
    # can_read_all, 0034 has_role).
    _run("0101_resolve_claims_multi_env.sql")
    op.get_bind().exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION auth.tenant_lineage() RETURNS int[] LANGUAGE sql STABLE AS $$
          SELECT CASE
            WHEN auth.portal() = 'client' OR auth.role() = 'client' THEN '{}'::int[]
            WHEN auth.jwt() ? 'tenant_ids'
              THEN ARRAY(SELECT jsonb_array_elements_text(auth.jwt() -> 'tenant_ids')::int)
            ELSE auth.tenant_lineage_of(auth.tenant_id())
          END $$;
        CREATE OR REPLACE FUNCTION auth.can_read_all() RETURNS boolean LANGUAGE sql STABLE AS $$
          SELECT COALESCE(auth.portal(), '') <> 'client'
             AND auth.has_permission('contacts:read_all') $$;
        CREATE OR REPLACE FUNCTION auth.has_role(p_role text) RETURNS boolean LANGUAGE sql STABLE AS $$
          SELECT (auth.jwt() ->> 'role') = p_role $$;
        """
    )
