"""contribution category on a payment instruction

Revision ID: 0220_instruction_category
Revises: 0219_action_step_uid
Create Date: 2026-10-05

``alembic_version.version_num`` is ``varchar(32)``; this id is 25 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0220_instruction_category"
down_revision: str | None = "0219_action_step_uid"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0220_instruction_category.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER TABLE platform.instructions DROP CONSTRAINT IF EXISTS instructions_category_chk;
        ALTER TABLE platform.instructions DROP COLUMN IF EXISTS category;
        """
    )
