"""Allow several providers in the same role for one shelf product

Revision ID: 0151_product_role_cardinality
Revises: 0150_model_charge_percent
Create Date: 2026-09-10
"""

from pathlib import Path

from alembic import op

revision: str = "0151_product_role_cardinality"
down_revision: str | None = "0150_model_charge_percent"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0151_product_platform_role_cardinality.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # No-op deliberately: the dropped index enforced a false cardinality and may no longer be
    # recreatable once two custodians legitimately map the same product.
    pass
