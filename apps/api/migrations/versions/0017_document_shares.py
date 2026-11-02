"""document share links (documents.shares)

Revision ID: 0017_document_shares
Revises: 0016_platform_instructions
Create Date: 2026-07-15

Time-limited public share tokens for documents (polymorphic for the files entity later). DDL in the
sibling SQL file; applied as the elevated migrator role. See docs/document-management.md.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017_document_shares"
down_revision: str | None = "0016_platform_instructions"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0017_document_shares.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS documents.shares CASCADE")
