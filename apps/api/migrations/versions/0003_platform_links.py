"""platform_links: the polymorphic provider external-id bridge

Revision ID: 0003_platform_links
Revises: 0002_platform_crm
Create Date: 2026-07-10

DDL in the sibling SQL file (migrations/sql/0003_platform_links.sql). See 0001_auth_core.py
for the loader rationale (runs as the migrator role).
"""

from pathlib import Path

from alembic import op

revision: str = "0003_platform_links"
down_revision: str | None = "0002_platform_crm"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0003_platform_links.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.platform_links CASCADE")
