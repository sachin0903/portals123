"""platform.products.is_pension is the display authority

Revision ID: 0141_is_pension_authority
Revises: 0140_product_shelf_completeness
Create Date: 2026-09-08
"""

from pathlib import Path

from alembic import op

revision: str = "0141_is_pension_authority"
down_revision: str | None = "0140_product_shelf_completeness"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    name = "0141_is_pension_authority.sql"
    sql = (_SQL_DIR / name).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Restores 0128's wording verbatim, because a downgrade puts back the statement that was
    # true of the code at that revision — the column's VALUES are untouched either way.
    op.get_bind().exec_driver_sql(
        "COMMENT ON COLUMN platform.products.is_pension IS "
        "'Whether this wrapper is a pension. DERIVED from product_type today and kept in step "
        "with it by 0128 -- five surfaces decide pension-ness from product_type, so this is not "
        "the authority. It exists for the case product_type cannot answer (a bond a firm treats "
        "as a pension wrapper); until something writes it deliberately, do not read it in "
        "preference to product_type.';"
    )
