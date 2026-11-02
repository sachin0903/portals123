"""one provider vocabulary: an enum, and platform.platforms dropped

Revision ID: 0143_one_provider_vocabulary
Revises: 0142_registry_dead_weight
Create Date: 2026-09-08
"""

from pathlib import Path

from alembic import op

revision: str = "0143_one_provider_vocabulary"
down_revision: str | None = "0142_registry_dead_weight"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    name = "0143_one_provider_vocabulary.sql"
    sql = (_SQL_DIR / name).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # NOT REVERSIBLE, and saying so beats a downgrade that half-works. Restoring
    # `platform.platforms` means re-minting surrogate ids and re-pointing eight columns at rows
    # whose ids no one recorded — every FK value would be invented. The forward path is another
    # migration.
    raise NotImplementedError(
        "0131 drops platform.platforms and replaces eight foreign keys with the value they "
        "pointed at. The surrogate ids are not recoverable, so a downgrade would fabricate them. "
        "Roll forward instead."
    )
