"""Morgan Lloyd as scheme administrator is SIPP-only

Revision ID: 0202_sipp_scheme_admin
Revises: 0201_provider_document_names
Create Date: 2026-09-23

The attach used `is_pension`, which is true for TPPENSION. Drop those mappings.
First applied as ``0184_sipp_scheme_admin``, then as ``0193_sipp_scheme_admin``.
"""

from pathlib import Path

from alembic import op

revision: str = "0202_sipp_scheme_admin"
down_revision: str | None = "0201_provider_document_names"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0202_sipp_scheme_admin.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The dropped mappings were wrong. Restoring them would retag TPPENSION.
    pass
