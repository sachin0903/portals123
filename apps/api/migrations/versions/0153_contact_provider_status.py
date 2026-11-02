"""the custodian's own status for a person, so Suspended can show without Deactivate

Revision ID: 0153_contact_provider_status
Revises: 0152_dataset_catalogue
Create Date: 2026-09-11
"""

from pathlib import Path

from alembic import op

revision: str = "0153_contact_provider_status"
down_revision: str | None = "0152_dataset_catalogue"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0153_contact_provider_status.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.execute("ALTER TABLE crm.contacts DROP COLUMN IF EXISTS provider_status")
