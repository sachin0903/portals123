"""an adviser fee on a payment or withdrawal names its own SEI fee type

Revision ID: 0265_adviser_fee_type
Revises: 0264_withdrawal_fee_disinvest
Create Date: 2026-10-20

Adds platform.instructions.adviser_fee_package_id — the fee TYPE a SEI adviser fee is charged
under, separate from fee_package_id (which stays the ad-hoc fee's own type). Constrained to the
movement kinds that can carry an adviser fee.
"""

from pathlib import Path

from alembic import op

revision: str = "0265_adviser_fee_type"
down_revision: str | None = "0264_withdrawal_fee_disinvest"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0265_adviser_fee_type.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE platform.instructions\n"
        "  DROP CONSTRAINT instructions_adviser_fee_package_movement_only,\n"
        "  DROP CONSTRAINT instructions_adviser_fee_package_tenant_fkey,\n"
        "  DROP COLUMN adviser_fee_package_id;"
    )
