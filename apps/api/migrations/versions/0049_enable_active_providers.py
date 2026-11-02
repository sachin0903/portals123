"""transfer providers: Active ones are available by default

Revision ID: 0049_enable_active_providers
Revises: 0048_providers_into_platform
Create Date: 2026-08-02

The DDL/DML lives in the sibling SQL file -- that file is the reviewable artifact; this script
just applies it. Runs as the elevated migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0049_enable_active_providers"
down_revision: str | None = "0048_providers_into_platform"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0049_enable_active_providers.sql")


def downgrade() -> None:
    # Switching every provider back off would be a data loss dressed as a rollback: by the time
    # this runs, some of those rows may have been enabled or disabled by a person and there is no
    # record of which. The default in app/materialise.py is what a revert should change.
    pass
