"""seed the model tenant with a default role set — the bootstrap a fresh install cannot do without

Revision ID: 0033_model_role_preset
Revises: 0032_published_version_immutable
Create Date: 2026-07-28

0001_auth_core.sql:631 seeds the model tenant blank and no migration ever created a role, so
provisioning.clone_model_roles cloned nothing, grant_portal_access raised LookupError and every
require_permission endpoint 403d on a fresh install. Seeds owner / adviser / compliance (platform)
+ client, with grants built from the slugs declared in app/permissions.py. Applied only when the
model tenant has no roles at all, so a configured install is left untouched (and re-running is a
no-op). DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033_model_role_preset"
down_revision: str | None = "0032_published_version_immutable"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Remove only the preset roles (grants cascade). Tenants cloned FROM the model keep their own
# copies — this does not un-provision a firm. Runs elevated: auth.roles is FORCE RLS.
_DOWN = """
SET LOCAL ROLE service_role;
DELETE FROM auth.roles
 WHERE slug IN ('owner', 'adviser', 'compliance', 'client')
   AND tenant_id IN (SELECT id FROM auth.tenants WHERE is_model);
RESET ROLE;
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0033_model_role_preset.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
