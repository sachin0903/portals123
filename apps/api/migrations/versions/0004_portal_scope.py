"""portal-aware scoping: the client portal as a hard trust boundary

Revision ID: 0004_portal_scope
Revises: 0003_platform_links
Create Date: 2026-07-10

DDL in migrations/sql/0004_portal_scope.sql. Re-defines auth.portal / tenant_lineage /
can_read_all / resolve_claims (all CREATE OR REPLACE — no schema change). Runs as the
migrator (a member of service_role, so it may replace the service_role-owned resolve_claims).
"""

from pathlib import Path

from alembic import op

revision: str = "0004_portal_scope"
down_revision: str | None = "0003_platform_links"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0004_portal_scope.sql")


def downgrade() -> None:
    # Restore the pre-portal function forms (0001 lineage/read_all, 0002 resolve_claims);
    # drop the portal() helper. No schema objects were added, so nothing to un-create.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION auth.tenant_lineage() RETURNS int[] LANGUAGE sql STABLE AS $$
          SELECT CASE
            WHEN auth.role() = 'client' THEN '{}'::int[]
            WHEN auth.jwt() ? 'tenant_ids'
              THEN ARRAY(SELECT jsonb_array_elements_text(auth.jwt() -> 'tenant_ids')::int)
            ELSE COALESCE(
              (SELECT array_agg(id) FROM auth.tenants WHERE auth.tenant_id() = ANY(lineage)),
              '{}'::int[])
          END $$;

        CREATE OR REPLACE FUNCTION auth.can_read_all() RETURNS boolean LANGUAGE sql STABLE AS $$
          SELECT auth.has_permission('contacts:read_all') $$;

        CREATE OR REPLACE FUNCTION auth.resolve_claims(p_workos_user_id text, p_workos_org_id text)
          RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = 'auth' AS $fn$
        DECLARE
          v_user_uuid uuid; v_tenant_id int; v_tenant_ids int[];
          v_contact_ids int[]; v_account_ids int[];
        BEGIN
          SELECT id, tenant_id INTO v_user_uuid, v_tenant_id FROM auth.users
            WHERE workos_user_id = p_workos_user_id AND deleted_at IS NULL;
          IF v_tenant_id IS NULL THEN
            SELECT id INTO v_tenant_id FROM auth.tenants WHERE workos_organisation_id = p_workos_org_id;
          END IF;
          IF v_tenant_id IS NOT NULL THEN
            SELECT COALESCE(array_agg(id), '{}'::int[]) INTO v_tenant_ids
              FROM auth.tenants WHERE v_tenant_id = ANY(lineage);
          ELSE v_tenant_ids := '{}'::int[]; END IF;
          IF v_user_uuid IS NOT NULL THEN
            SELECT COALESCE(array_agg(DISTINCT cid), '{}'::int[]) INTO v_contact_ids FROM (
              SELECT contact_id AS cid FROM crm.contact_users   WHERE user_id = v_user_uuid
              UNION
              SELECT contact_id AS cid FROM crm.client_advisers WHERE adviser_id = v_user_uuid
            ) s;
          ELSE v_contact_ids := '{}'::int[]; END IF;
          IF array_length(v_contact_ids, 1) IS NOT NULL THEN
            SELECT COALESCE(array_agg(DISTINCT aid), '{}'::int[]) INTO v_account_ids FROM (
              SELECT id AS aid FROM platform.accounts WHERE contact_id = ANY(v_contact_ids)
              UNION
              SELECT account_id AS aid FROM crm.account_contacts
                WHERE contact_id = ANY(v_contact_ids) AND account_id IS NOT NULL
            ) s;
          ELSE v_account_ids := '{}'::int[]; END IF;
          RETURN jsonb_build_object(
            'user_uuid', v_user_uuid, 'tenant_id', v_tenant_id,
            'tenant_ids', to_jsonb(v_tenant_ids),
            'contact_ids', to_jsonb(v_contact_ids), 'account_ids', to_jsonb(v_account_ids));
        END; $fn$;
        ALTER FUNCTION auth.resolve_claims(text, text) OWNER TO service_role;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS auth.portal()")
