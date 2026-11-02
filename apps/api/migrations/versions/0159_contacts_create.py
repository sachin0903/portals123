"""Grant backfill for contacts:create (split out of contacts:write)

Revision ID: 0159_contacts_create
Revises: 0158_onboarding_adviser
Create Date: 2026-09-13

Pure grants, expand-only and idempotent: the new slug goes to every existing role
holding contacts:write, plus the model tenant's owner and adviser. The registry
addition itself lives in app/permissions.py (sync_permissions upserts on startup);
SQL in the sibling file, applied as the elevated migrator role (SET LOCAL ROLE
service_role inside — auth.* is FORCE RLS).
"""

from pathlib import Path

from alembic import op

revision: str = "0159_contacts_create"
down_revision: str | None = "0158_onboarding_adviser"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0159_contacts_create.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Remove only the grants of the split slug (the permission row stays:
    # sync_permissions owns the catalogue, and other grants may have been made
    # deliberately since — a downgrade of a pure-grants backfill is best-effort
    # by nature).
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL ROLE service_role;
        DELETE FROM auth.role_permissions rp
         USING auth.permissions p
         WHERE p.id = rp.permission_id
           AND p.slug = 'contacts:create';
        RESET ROLE;
        """
    )
