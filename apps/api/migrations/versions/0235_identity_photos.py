"""identity photos: a portal client's ID photos, held encrypted until their check is finished

Revision ID: 0235_identity_photos
Revises: 0234_txn_public_id
Create Date: 2026-10-07

``alembic_version.version_num`` is ``varchar(32)``; this id is 20 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0235_identity_photos"
down_revision: str | None = "0234_txn_public_id"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0235_identity_photos.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP TABLE IF EXISTS compliance.identity_photos")
