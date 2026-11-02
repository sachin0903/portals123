"""drop the legacy actions engine tables (baton is the sole engine; docs/baton.md)

Revision ID: 0262_drop_legacy_actions
Revises: 0261_baton
Create Date: 2026-10-17

Drops ``actions.runs/steps/http_requests/events/subjects/run_approvers``. Firm policy
(``actions.policies`` / ``actions.policy_roles``) stays. Irreversible: the run history is not
recreated on downgrade.
"""

from pathlib import Path

from alembic import op

revision: str = "0262_drop_legacy_actions"
down_revision: str | None = "0261_baton"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0262_drop_legacy_actions.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    raise RuntimeError(
        "0262 dropped the legacy actions engine tables; the run history is not recreatable. "
        "Restore from a backup if you need it."
    )
