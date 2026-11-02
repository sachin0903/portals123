"""share the array-shaped client/staff user type contract

Revision ID: 0121_user_types
Revises: 0120_access_repair
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0121_user_types"
down_revision: str | None = "0120_access_repair"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0121_user_types.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Restore the 0120 trigger body before removing the column it reads. The full 0120 SQL also
    # replays its idempotent repair, which is safe and keeps this downgrade considerably less
    # fragile than carrying a second copy of the function here.
    sql = (_SQL_DIR / "0120_access_repair.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)
    op.execute("DROP TRIGGER IF EXISTS users_sync_type_contract ON auth.users")
    op.execute("DROP FUNCTION IF EXISTS auth.sync_user_type_contract()")
    op.execute("ALTER TABLE auth.users DROP COLUMN IF EXISTS type")
