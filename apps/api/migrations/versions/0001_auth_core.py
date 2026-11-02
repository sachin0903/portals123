"""auth core: roles, auth schema, RLS helpers + resolve_claims, audit spine

Revision ID: 0001_auth_core
Revises:
Create Date: 2026-06-24

The DDL lives in the sibling SQL file (migrations/sql/0001_auth_core.sql) -- that file
is the reviewable artifact; this script just applies it. Runs as the elevated migrator
role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_auth_core"
down_revision: str | None = None
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
    _run("0001_auth_core.sql")


def downgrade() -> None:
    # Greenfield: tear the foundation back down. The NOLOGIN principal roles
    # (anon/authenticated/service_role) are left in place -- they own nothing once the
    # schemas are gone, and re-running upgrade re-grants them idempotently.
    op.execute("DROP SCHEMA IF EXISTS audit CASCADE")
    op.execute("DROP SCHEMA IF EXISTS auth CASCADE")
    op.execute("DROP FUNCTION IF EXISTS public.set_updated_at() CASCADE")
