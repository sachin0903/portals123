"""Remember a SEI money-out outside the request that sends it.

Revision ID: 0260_sei_money_holds
Revises: 0259_sei_product_custodian
Create Date: 2026-10-16

``alembic_version.version_num`` is ``varchar(32)``; this id is 20 characters.

The pending booking lives in the request transaction, which still holds its
unique key. A flag on that row rolls back with the request, and the retry pays
SEI again under a new instruction uid. The hold is a separate committed row.
"""

from pathlib import Path

from alembic import op

revision: str = "0260_sei_money_holds"
down_revision: str | None = "0259_sei_product_custodian"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0260_sei_money_holds.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "SET LOCAL lock_timeout = '5s'; DROP TABLE IF EXISTS platform.sei_money_holds;"
    )
