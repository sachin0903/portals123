"""Grant backfill for the six permission splits (instructions:*, documents:send,
illustrations:write, forms:submit, pages:write, platforms:query)

Revision ID: 0042_permission_splits
Revises: 0041_action_events
Create Date: 2026-08-03

Pure grants, expand-only and idempotent: each new slug goes to every existing role holding the
parent it was split from, plus the model tenant's preset mapping. The registry addition itself
lives in app/permissions.py (sync_permissions upserts on startup); SQL in the sibling file,
applied as the elevated migrator role (SET LOCAL ROLE service_role inside — auth.* is FORCE RLS).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0042_permission_splits"
down_revision: str | None = "0041_action_events"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0042_permission_splits.sql")


def downgrade() -> None:
    # Remove only the grants of the split slugs (the permission rows stay: sync_permissions
    # owns the catalogue, and other grants may have been made deliberately since — a downgrade
    # of a pure-grants backfill is best-effort by nature).
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL ROLE service_role;
        DELETE FROM auth.role_permissions rp
         USING auth.permissions p
         WHERE p.id = rp.permission_id
           AND p.slug IN ('instructions:read', 'instructions:write', 'documents:send',
                          'illustrations:write', 'forms:submit', 'pages:write',
                          'platforms:query');
        RESET ROLE;
        """
    )
