"""separate staff from client profiles and assign role sets

Revision ID: 0119_access_profiles_roles
Revises: 0118_comms_request
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0119_access_profiles_roles"
down_revision: str | None = "0118_comms_request"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0119_access_profiles_and_multi_roles.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP TRIGGER IF EXISTS roles_membership_scope ON auth.roles;
        DROP FUNCTION IF EXISTS auth.enforce_role_membership_scope();
        DROP TRIGGER IF EXISTS organisation_memberships_primary_role
          ON auth.organisation_memberships;
        DROP FUNCTION IF EXISTS auth.sync_primary_membership_role();
        DROP TRIGGER IF EXISTS membership_roles_scope ON auth.membership_roles;
        DROP FUNCTION IF EXISTS auth.enforce_membership_role_scope();
        DROP TABLE IF EXISTS auth.membership_roles;
        DROP TRIGGER IF EXISTS users_access_kind ON auth.users;
        DROP FUNCTION IF EXISTS auth.enforce_user_access_kind();
        DROP TRIGGER IF EXISTS organisation_memberships_access_kind
          ON auth.organisation_memberships;
        DROP FUNCTION IF EXISTS auth.enforce_membership_access_kind();
        ALTER TABLE auth.users DROP COLUMN IF EXISTS access_kind;
        """
    )
    # Restore the pre-0119 claim minter, which reads the membership's single role_id.
    legacy = (_SQL_DIR / "0107_app_only_portal.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(legacy)
