"""platform + crm domain: contacts (the client), accounts, transactions, client-scoped RLS

Revision ID: 0002_platform_crm
Revises: 0001_auth_core
Create Date: 2026-07-10

The DDL lives in the sibling SQL file (migrations/sql/0002_platform_crm.sql) -- that file
is the reviewable artifact; this script just applies it. Runs as the elevated migrator
role (see migrations/env.py), which is a member of service_role so it may CREATE OR REPLACE
the service_role-owned auth.resolve_claims.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_platform_crm"
down_revision: str | None = "0001_auth_core"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    # See 0001_auth_core.py::_run -- psycopg pyformat means every literal '%' must be
    # doubled so the server sees a single '%'. (No '%' in this file today, but keep the
    # same safe path as 0001.)
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0002_platform_crm.sql")


def downgrade() -> None:
    # Drop the two domain schemas; restore auth.resolve_claims to its 0001 form (empty
    # contact/account scope) so the foundation still resolves after a down-migration.
    op.execute("DROP SCHEMA IF EXISTS crm CASCADE")
    op.execute("DROP SCHEMA IF EXISTS platform CASCADE")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION auth.resolve_claims(p_workos_user_id text, p_workos_org_id text)
          RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = 'auth' AS $fn$
        DECLARE
          v_user_uuid uuid;
          v_tenant_id int;
          v_tenant_ids int[];
        BEGIN
          SELECT id, tenant_id INTO v_user_uuid, v_tenant_id FROM auth.users
            WHERE workos_user_id = p_workos_user_id AND deleted_at IS NULL;
          IF v_tenant_id IS NULL THEN
            SELECT id INTO v_tenant_id FROM auth.tenants
              WHERE workos_organisation_id = p_workos_org_id;
          END IF;
          IF v_tenant_id IS NOT NULL THEN
            SELECT COALESCE(array_agg(id), '{}'::int[]) INTO v_tenant_ids
              FROM auth.tenants WHERE v_tenant_id = ANY(lineage);
          ELSE
            v_tenant_ids := '{}'::int[];
          END IF;
          RETURN jsonb_build_object(
            'user_uuid',   v_user_uuid,
            'tenant_id',   v_tenant_id,
            'tenant_ids',  to_jsonb(v_tenant_ids),
            'contact_ids', '[]'::jsonb,
            'account_ids', '[]'::jsonb
          );
        END;
        $fn$;
        """
    )
    op.execute("ALTER FUNCTION auth.resolve_claims(text, text) OWNER TO service_role")
