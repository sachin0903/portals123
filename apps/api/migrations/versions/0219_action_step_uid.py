"""public id for an action step

Revision ID: 0219_action_step_uid
Revises: 0218_allowance_tax_year
Create Date: 2026-10-05

``alembic_version.version_num`` is ``varchar(32)``; this id is 20 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0219_action_step_uid"
down_revision: str | None = "0218_allowance_tax_year"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0219_action_step_uid.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP INDEX IF EXISTS actions_steps_uid_idx;
        ALTER TABLE actions.steps DROP COLUMN IF EXISTS uid;
        """
    )
