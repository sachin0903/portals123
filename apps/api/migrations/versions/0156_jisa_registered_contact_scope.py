"""client portal sees JISA child via registered_contact

Revision ID: 0156_jisa_contact_scope
Revises: 0155_registered_contact
Create Date: 2026-09-13
"""

from pathlib import Path

from alembic import op

revision: str = "0156_jisa_contact_scope"
down_revision: str | None = "0155_registered_contact"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0156_jisa_registered_contact_scope.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Restoring 0119 verbatim would drop the JISA union; leave the function as-is.
    pass
