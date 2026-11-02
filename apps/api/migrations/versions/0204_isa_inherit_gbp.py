"""ISA/JISA with no currencies list inherit GBP, not the firm's whole list

Revision ID: 0204_isa_inherit_gbp
Revises: 0203_provider_document_dates
Create Date: 2026-09-23

First applied as ``0186_isa_inherit_gbp``, then as ``0195_isa_inherit_gbp``.
The book-sink merge omits allowed_currencies. The trigger used to copy the
firm's list, so a multi-currency firm failed ISA inserts.
"""

from pathlib import Path

from alembic import op

revision: str = "0204_isa_inherit_gbp"
down_revision: str | None = "0203_provider_document_dates"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0204_isa_inherit_gbp.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The previous trigger body is 0174. Restoring it would re-break the merge.
    pass
