"""is_system() grants on the `system` permission only, never the token's role name

Revision ID: 0034_is_system_permission_only
Revises: 0033_model_role_preset
Create Date: 2026-07-28

Closes the SQL half of the "a WorkOS role named admin is a superuser" hole. `auth.is_system()`
granted on `(auth.jwt() ->> 'role') IN ('system','admin')`, and that role name comes from the
tenant's own WorkOS environment — so a firm naming a role "admin" passed every RLS permission gate,
since has_permission() and can_read_all() both short-circuit on is_system(). The application side
(app/auth.py) and the generated UI gate (packages/api-client/permissions.ts) were fixed first; this
brings the database into agreement so the two cannot drift again.

`auth.has_role()` also loses its is_system() short-circuit: asking "is this the adviser role" is a
legitimate tenant-scoped question, but it must not confer superuser.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0034_is_system_permission_only"
down_revision: str | None = "0033_model_role_preset"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Restores 0001_auth_core.sql's definitions verbatim -- deliberately reinstating the role-name
# bypass, which is what "downgrade" means here.
_DOWN = """
CREATE OR REPLACE FUNCTION auth.is_system() RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT (auth.jwt() ->> 'role') IN ('system', 'admin')
      OR ('system' = ANY(auth.jwt_permissions())) $$;

CREATE OR REPLACE FUNCTION auth.has_role(p_role text) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT auth.is_system() OR (auth.jwt() ->> 'role') = p_role $$;
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0034_is_system_permission_only.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
