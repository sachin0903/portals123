"""auth.member_permissions -- resolve a user's permissions from the DB, not the JWT claim

Revision ID: 0093_member_permissions
Revises: 0092_rule_versions
Create Date: 2026-08-14

DDL in the sibling SQL. See its header comment for why this is a new function rather than an
extra key on auth.resolve_claims's existing return value.
"""

from pathlib import Path

from alembic import op

revision: str = "0093_member_permissions"
down_revision: str | None = "0092_rule_versions"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0093_member_permissions.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql("DROP FUNCTION IF EXISTS auth.member_permissions(int[])")
