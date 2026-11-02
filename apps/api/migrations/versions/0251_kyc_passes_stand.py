"""kyc passes stand: an identity or AML pass does not lapse

Revision ID: 0251_kyc_passes_stand
Revises: 0250_identity_upload
Create Date: 2026-10-09

``alembic_version.version_num`` is ``varchar(32)``; this id is 21 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0251_kyc_passes_stand"
down_revision: str | None = "0250_identity_upload"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0251_kyc_passes_stand.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The horizons it cleared are not kept: a pass made before it reads as standing either way.
    pass
