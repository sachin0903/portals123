"""tenant storage: storage schema + per-tenant object-storage registry

Revision ID: 0027_tenant_storage
Revises: 0029_dataset_sample_data
Create Date: 2026-07-16

The DDL lives in the sibling SQL file (migrations/sql/0027_tenant_storage.sql) -- that
file is the reviewable artifact; this script just applies it. Runs as the elevated
migrator role (see migrations/env.py).

The number is lower than its parent's: the storage/platforms line and the tasks line
were authored in parallel and both took 0027-0029. Revision *ids* are what alembic
records in alembic_version and what the docs reference, so this chain keeps its ids
and is simply re-pointed after the tasks chain -- renaming them would orphan the
version already recorded in any environment that has applied them.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027_tenant_storage"
down_revision: str | None = "0029_dataset_sample_data"
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
    _run("0027_tenant_storage.sql")


def downgrade() -> None:
    # The schema currently holds only the storage registry; later storage tables
    # (e.g. the physical file registry) will ship their own migrations + downgrades.
    op.execute("DROP SCHEMA IF EXISTS storage CASCADE")
