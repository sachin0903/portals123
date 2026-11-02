"""actions: the domain-action registry

Revision ID: 0215_actions
Revises: 0214_sessions
Create Date: 2026-10-01

``alembic_version.version_num`` is ``varchar(32)``; this id is 13 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0215_actions"
down_revision: str | None = "0214_sessions"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0215_actions.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP SCHEMA IF EXISTS actions CASCADE;
        DELETE FROM auth.role_permissions rp
          USING auth.permissions p
          WHERE p.id = rp.permission_id AND p.slug IN ('actions:read', 'actions:write');
        DELETE FROM auth.permissions WHERE slug IN ('actions:read', 'actions:write');
        """
    )
