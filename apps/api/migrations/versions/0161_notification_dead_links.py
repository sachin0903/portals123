"""Repair notification links that name no route

Revision ID: 0161_notification_dead_links
Revises: 0160_product_kickoff_rules
Create Date: 2026-09-14

Data-only, and the data is the point: a notification's link is stored on the row,
so correcting the catalogue fixes only what is written from here on and every
notice already sent keeps pointing at nothing. SQL in the sibling file, which
runs as service_role inside — notifications.notifications is FORCE RLS, and the
migrator is not BYPASSRLS, so without that the backfill updates zero rows and
commits happily.
"""

from pathlib import Path

from alembic import op

revision: str = "0161_notification_dead_links"
down_revision: str | None = "0160_product_kickoff_rules"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0161_notification_dead_links.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Deliberately empty. The old values named routes that do not exist, so
    # restoring them would only put readers back in front of a 404 — and the
    # rewrite is not reversible anyway: several distinct dead links collapse onto
    # one repaired value, and the serials that resolved to nothing became NULL.
    pass
