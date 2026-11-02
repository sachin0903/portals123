"""platform.products: the two missing wrappers, and is_pension corrected

Revision ID: 0140_product_shelf_completeness
Revises: 0139_client_sections
Create Date: 2026-09-08
"""

from pathlib import Path

from alembic import op

revision: str = "0140_product_shelf_completeness"
down_revision: str | None = "0139_client_sections"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    name = "0140_product_shelf_completeness.sql"
    sql = (_SQL_DIR / name).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The seeded shelf items go; `is_pension` is left CORRECT rather than restored to the wrong
    # value it held — a downgrade undoes a schema change, not a data repair.
    op.get_bind().exec_driver_sql(
        "DELETE FROM platform.products "
        "WHERE product_type IN ('TPONBOND', 'TPOFFBOND') AND tenant_id IS NOT NULL;"
    )
