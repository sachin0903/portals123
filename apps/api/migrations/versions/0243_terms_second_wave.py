"""l10n.terms: six more nouns firms rename — document, note, task, illustration, journey, beneficiary

Revision ID: 0243_terms_second_wave
Revises: 0242_stmt_columns
Create Date: 2026-10-08

The seed and its reasoning live in the sibling SQL file -- that file is the reviewable artifact;
this script just applies it. Runs as the elevated migrator role (migrations/env.py), which is why
the SQL sets `service_role` for the insert itself.
"""

from pathlib import Path

from alembic import op

revision: str = "0243_terms_second_wave"
down_revision: str | None = "0242_stmt_columns"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0243_terms_second_wave.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # `l10n.overrides.term_key` cascades from here, so removing a term removes any firm's chosen
    # word for it. Deliberate on a downgrade -- the portals would no longer read the key, and a
    # row nothing reads is worse than none -- but it is why this is a real DELETE and not a no-op.
    op.get_bind().exec_driver_sql(
        "SET LOCAL ROLE service_role;\n"
        "DELETE FROM l10n.terms WHERE key IN "
        "  ('document', 'note', 'task', 'illustration', 'journey', 'beneficiary');\n"
        "RESET ROLE"
    )
