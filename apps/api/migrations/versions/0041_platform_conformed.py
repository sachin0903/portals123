"""platform conformed: orgs, instruments, valuations, model_platforms

Revision ID: 0041_platform_conformed
Revises: 0042_permission_splits
Create Date: 2026-07-29

The DDL lives in the sibling SQL file (migrations/sql/0030_platform_conformed.sql) -- that
file is the reviewable artifact; this script just applies it. Runs as the elevated
migrator role (see migrations/env.py).

Completes the canonical model in docs/platform-model.md: the four entities the conformed
provider layers materialise into, plus the FK columns wiring them to what 0002 built.
Expand phase only -- new tables and nullable columns.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0041_platform_conformed"
# Rebased onto main (Aug 2026): this chain and main's (0041_action_events ->
# 0042_permission_splits) both grew from 0040_file_types, which alembic reports as two
# heads and refuses to `upgrade head`. Re-pointed here rather than renumbering the 15
# files behind it -- their revision ids are cited by docs and by the SQL filenames.
down_revision: str | None = "0042_permission_splits"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    # exec_driver_sql sends the literal SQL straight to psycopg (no SQLAlchemy bind-param
    # parsing, so `::cast` is safe) as a single multi-statement script. psycopg uses
    # pyformat, so it still scans for `%` placeholders -- double every `%` (in RAISE/
    # format() bodies) so psycopg collapses `%%` back to a literal `%` for the server.
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0041_platform_conformed.sql")


def downgrade() -> None:
    op.execute("ALTER TABLE platform.accounts DROP COLUMN IF EXISTS is_discretionary")
    op.execute("ALTER TABLE platform.transactions DROP COLUMN IF EXISTS instrument_id")
    op.execute("ALTER TABLE crm.contacts DROP COLUMN IF EXISTS org_id")
    op.execute("ALTER TABLE platform.accounts DROP COLUMN IF EXISTS org_id")
    op.execute("DROP TABLE IF EXISTS platform.model_platforms")
    op.execute("DROP TABLE IF EXISTS platform.valuations")
    op.execute("DROP TABLE IF EXISTS platform.instruments")
    op.execute("DROP TABLE IF EXISTS platform.orgs")
    op.execute("DROP TYPE IF EXISTS platform.org_type")
