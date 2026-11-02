"""actions: approval is a set of roles

Revision ID: 0216_action_approvers
Revises: 0215_actions
Create Date: 2026-10-02

``alembic_version.version_num`` is ``varchar(32)``; this id is 21 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0216_action_approvers"
down_revision: str | None = "0215_actions"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0216_action_approvers.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP TABLE IF EXISTS actions.run_approvers;
        DELETE FROM actions.policy_roles WHERE kind = 'approve';
        ALTER TABLE actions.policy_roles DROP CONSTRAINT IF EXISTS policy_roles_kind_check;
        ALTER TABLE actions.policy_roles DROP CONSTRAINT IF EXISTS policy_roles_pkey;
        ALTER TABLE actions.policy_roles DROP COLUMN IF EXISTS kind;
        ALTER TABLE actions.policy_roles ADD PRIMARY KEY (policy_id, role_id);
        """
    )
