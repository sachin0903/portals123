"""identity photos by check: an identity photo is kept until its check is decided

Revision ID: 0252_identity_photos_by_check
Revises: 0251_kyc_passes_stand
Create Date: 2026-10-12

``alembic_version.version_num`` is ``varchar(32)``; this id is 29 characters.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0252_identity_photos_by_check"
down_revision: str | None = "0251_kyc_passes_stand"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0252_identity_photos_by_check.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DELETE FROM compliance.identity_photos WHERE case_id IS NULL; "
        "ALTER TABLE compliance.identity_photos DROP CONSTRAINT IF EXISTS "
        "identity_photos_owner_check, DROP COLUMN IF EXISTS check_id, "
        "ALTER COLUMN case_id SET NOT NULL, ALTER COLUMN step_key SET NOT NULL"
    )
