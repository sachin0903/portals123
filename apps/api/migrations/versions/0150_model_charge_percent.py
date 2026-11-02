"""platform.models.model_charge: scale Seccl's fraction to 0-100 percent

Revision ID: 0150_model_charge_percent
Revises: 0149_platform_illustrations
Create Date: 2026-09-10
"""

from pathlib import Path

from alembic import op

revision: str = "0150_model_charge_percent"
down_revision: str | None = "0149_platform_illustrations"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0150_model_charge_percent.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Not reversed. The cutoff cannot tell a backfilled 0.20 (was 0.002) from a catalogue
    # edit that was already 0.20%, and dividing both would recreate the illustration bug
    # for every model a firm had set by hand.
    pass
