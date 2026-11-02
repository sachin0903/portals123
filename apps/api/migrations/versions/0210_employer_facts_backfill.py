"""crm: the fact-find's employer answers, out of `data` and into the Employer card's columns

Revision ID: 0210_employer_facts_backfill
Revises: 0209_inbound_per_provider
Create Date: 2026-09-30

``alembic_version.version_num`` is ``varchar(32)``; this id is 28 characters.

Data-only. 0139 created the columns and the onboarding mapping kept writing to
``data.pension.employerContributions.*``; the seed now targets the columns, and this moves the
rows written before it did. Not reversible — the ``data`` keys are dropped once copied, and
putting them back would restate facts the columns now own.
"""

from pathlib import Path

from alembic import op

revision: str = "0210_employer_facts_backfill"
down_revision: str | None = "0209_inbound_per_provider"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0210_employer_facts_backfill.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # A backfill has no schema to undo; the copied values are correct in either direction.
    pass
