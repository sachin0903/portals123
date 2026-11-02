"""baton: the durable-action runtime tables (docs/baton.md)

Revision ID: 0261_baton
Revises: 0263_client_groups
Create Date: 2026-10-16

Re-chained onto main's head (0263_client_groups) when feat/baton-runtime was rebased, so the
baton chain runs after main's fatca/client-group work. The filename keeps its original 0261 number
(this repo tolerates that drift); the chain is what alembic follows.

``alembic_version.version_num`` is ``varchar(32)``; this id is 11 characters.

Creates schema ``baton`` and its runtime tables. These coexist with the legacy
``actions.*`` engine; firm policy stays in ``actions.policies``. Downgrade drops
the schema.
"""

from pathlib import Path

from alembic import op

revision: str = "0261_baton"
down_revision: str | None = "0263_client_groups"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0261_baton.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP SCHEMA IF EXISTS baton CASCADE;
        """
    )
