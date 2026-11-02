"""auth: scope the tenant directory, the permission catalogue and the grant graph + audit grants

Revision ID: 0030_auth_directory_scope
Revises: 0029_dataset_sample_data
Create Date: 2026-07-28

Closes the three USING (true) reads left in 0001_auth_core: auth.tenants (any authenticated
principal, including a client end-user of one firm, could enumerate every firm on the instance
including its whole white-label theme), auth.permissions and auth.role_permissions (the grant
graph). The recursion hazard the 0001 comment describes is real, so the two auth.tenants scans the
RLS helpers perform move into SECURITY DEFINER accessors owned by service_role before the table is
scoped. Those accessors take a caller-supplied argument and run BYPASSRLS, so each guards its own
argument internally (own org / own tenant, or auth.is_system()) — otherwise closing the table read
would simply move the same cross-tenant read to the function boundary. Also revokes the request
role's INSERT/UPDATE/DELETE on auth.api_keys (a latent key-minting path with no permission gate) and
attaches the audit spine to auth.role_permissions — permission grants/revocations previously left no
trace at all. DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030_auth_directory_scope"
down_revision: str | None = "0029_dataset_sample_data"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Restore the 0001 USING (true) policies, the 0001/0004 helper bodies (inline scans), and detach
# the audit trigger. The definer accessors are dropped last (the helpers stop referencing them).
_DOWN = """
DROP TRIGGER IF EXISTS audit_row ON auth.role_permissions;

DROP POLICY auth_role_permissions_read ON auth.role_permissions;
CREATE POLICY auth_role_permissions_read ON auth.role_permissions FOR SELECT TO authenticated
  USING (true);

DROP POLICY auth_permissions_read ON auth.permissions;
CREATE POLICY auth_permissions_read ON auth.permissions FOR SELECT TO authenticated USING (true);

DROP POLICY auth_tenants_scoped_read ON auth.tenants;
CREATE POLICY auth_tenants_auth_read ON auth.tenants FOR SELECT TO authenticated USING (true);

CREATE OR REPLACE FUNCTION auth.tenant_id() RETURNS int LANGUAGE sql STABLE AS $$
  SELECT COALESCE(
    NULLIF(auth.jwt() ->> 'tenant_id', '')::int,
    (SELECT t.id FROM auth.tenants t WHERE t.workos_organisation_id = auth.workos_org())
  ) $$;

CREATE OR REPLACE FUNCTION auth.tenant_lineage() RETURNS int[] LANGUAGE sql STABLE AS $$
  SELECT CASE
    WHEN auth.portal() = 'client' OR auth.role() = 'client' THEN '{}'::int[]
    WHEN auth.jwt() ? 'tenant_ids'
      THEN ARRAY(SELECT jsonb_array_elements_text(auth.jwt() -> 'tenant_ids')::int)
    ELSE COALESCE(
      (SELECT array_agg(id) FROM auth.tenants WHERE auth.tenant_id() = ANY(lineage)),
      '{}'::int[])
  END $$;

DROP FUNCTION IF EXISTS auth.tenant_lineage_of(int);
DROP FUNCTION IF EXISTS auth.tenant_by_workos_org(text);
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0030_auth_directory_scope.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
