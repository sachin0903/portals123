"""aml: the local mirror of Creditsafe KYC Protect (profiles/searches/hits + sync state)

Revision ID: 0037_aml_creditsafe
Revises: 0036_merge_platforms_audit
Create Date: 2026-07-29

The DDL lives in the sibling SQL file (migrations/sql/0037_aml_creditsafe.sql) -- that
file is the reviewable artifact; this script just applies it. Runs as the elevated
migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0037_aml_creditsafe"
down_revision: str | None = "0036_merge_platforms_audit"
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
    _run("0037_aml_creditsafe.sql")


def downgrade() -> None:
    # The whole schema is a disposable read model -- Creditsafe remains the system of
    # record, so dropping it loses nothing that a re-sync cannot rebuild. CASCADE takes
    # the audit triggers with the tables (the audit.log rows they wrote are append-only
    # and stay).
    op.execute("DROP SCHEMA IF EXISTS aml CASCADE")
