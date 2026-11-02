"""SEI allowance tax year is stated by account details

Revision ID: 0218_allowance_tax_year
Revises: 0217_instrument_ocf
Create Date: 2026-10-05

``alembic_version.version_num`` is ``varchar(32)``; this id is 23 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0218_allowance_tax_year"
down_revision: str | None = "0217_instrument_ocf"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0218_allowance_tax_year.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "COMMENT ON COLUMN platform.accounts.allowance_tax_year IS "
        "'Which tax year allowance_used/allowance_limit cover. NULL on every row: SEI states "
        "no year and Seccl states one per clientProduct, so neither conformed model can fill "
        "it. Read by nothing — and the max()-not-sum() rule on the two figures above is "
        "correct only because of that.'"
    )
