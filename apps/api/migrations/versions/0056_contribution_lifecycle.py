"""Contribution/transfer provider status mirrors, cancelled terminal, match provenance

Revision ID: 0056_contribution_lifecycle
Revises: 0055_platforms_unique_provider
Create Date: 2026-08-04

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0056_contribution_lifecycle"
down_revision: str | None = "0055_platforms_unique_provider"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0056_contribution_lifecycle.sql")


def downgrade() -> None:
    # Restore the pre-0056 CHECKs before dropping the columns they do not mention, so a
    # downgrade cannot leave a row at a status the narrower constraint forbids.
    op.execute(
        "UPDATE platform.contributions SET status = 'manual_intervention' "
        "WHERE status = 'cancelled'"
    )
    op.execute("UPDATE platform.transfers SET status = 'in_progress' WHERE status = 'funds_sent'")

    for constraint in (
        "contributions_ml_status_known",
        "contributions_cancelled_explained",
        "contributions_status_check",
    ):
        op.execute(f"ALTER TABLE platform.contributions DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute(
        "ALTER TABLE platform.contributions ADD CONSTRAINT contributions_status_check CHECK ("
        "status IN ('identified', 'awaiting_confirmation', 'confirmed', 'ml_recorded', "
        "'ras_applied', 'ras_received', 'tax_relief_received', 'reconciled', "
        "'manual_intervention'))"
    )
    op.execute("DROP INDEX IF EXISTS platform.contributions_ml_status_idx")
    for column in ("ml_status", "ml_status_at", "cancelled_at", "cancelled_reason"):
        op.execute(f"ALTER TABLE platform.contributions DROP COLUMN IF EXISTS {column}")

    for constraint in ("transfers_ml_status_known", "transfers_status_check"):
        op.execute(f"ALTER TABLE platform.transfers DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute(
        "ALTER TABLE platform.transfers ADD CONSTRAINT transfers_status_check CHECK ("
        "status IN ('requested', 'submitted', 'in_progress', 'assets_received', 'completed', "
        "'cancelled', 'manual_intervention'))"
    )
    for column in ("ml_status", "ml_status_at", "ceding_status"):
        op.execute(f"ALTER TABLE platform.transfers DROP COLUMN IF EXISTS {column}")

    for column in ("matched_by", "matched_transaction_id"):
        op.execute(f"ALTER TABLE platform.transaction_expectations DROP COLUMN IF EXISTS {column}")
