"""event vocabulary: historic event types -> the declared resource.verb names

Revision ID: 0096_event_vocabulary
Revises: 0095_audit_aml_carveout
"""

from pathlib import Path

from alembic import op

revision: str = "0096_event_vocabulary"
down_revision: str | None = "0095_audit_aml_carveout"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    # `exec_driver_sql` with `%` doubled, the convention every SQL-first migration here uses (see
    # 0095): psycopg reads `%` as a placeholder, and this file has `LIKE 'action.%'` in its straggler
    # check. `op.execute()` is NOT interchangeable — it wraps the text for SQLAlchemy, which ran this
    # file to completion and applied none of it, leaving the revision stamped and the rows untouched.
    sql = (_SQL_DIR / "0096_event_vocabulary.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    """Deliberately not reversible — see the migration's own note.

    The rename collapses several old names onto one new one (the ops door and the firm door for one
    act), so there is no unique inverse: reversing `email_template.created` cannot know which door a
    row came through. Guessing would write a name that never described that row.
    """
    raise NotImplementedError(
        "0096 is one-way: the event-type rename is many-to-one, so the old name cannot be derived "
        "from the new one. Restore from a backup, or re-derive from audit.actions.route."
    )
