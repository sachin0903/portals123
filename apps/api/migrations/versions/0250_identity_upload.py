"""identity upload: who uploaded an identity check's photos, when it was not the client

Revision ID: 0250_identity_upload
Revises: 0250_staff_notes
Create Date: 2026-10-08

``alembic_version.version_num`` is ``varchar(32)``; this id is 20 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0250_identity_upload"
down_revision: str | None = "0250_staff_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0250_identity_upload.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE compliance.checks DROP COLUMN IF EXISTS upload_comment, "
        "DROP COLUMN IF EXISTS uploaded_via, DROP COLUMN IF EXISTS uploaded_by"
    )
