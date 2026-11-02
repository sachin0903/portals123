"""Legal terms catalogue + acceptances

Revision ID: 0184_terms
Revises: 0183_txn_reference_search
Create Date: 2026-09-22
"""

from pathlib import Path

from alembic import op

revision: str = "0184_terms"
down_revision: str | None = "0183_txn_reference_search"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0184_terms.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP SCHEMA IF EXISTS terms CASCADE;
        DELETE FROM auth.permissions
         WHERE slug IN (
            'terms:read', 'terms:write',
            'platform_terms:read', 'platform_terms:write'
         );
        """
    )
