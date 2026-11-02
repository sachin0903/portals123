"""compliance: ComplyCube's risk profile for each client (client_risk)

Revision ID: 0227_complycube_client_risk
Revises: 0226_complycube_polled_at
Create Date: 2026-10-02
"""

from pathlib import Path

from alembic import op

revision: str = "0227_complycube_client_risk"
down_revision: str | None = "0226_complycube_polled_at"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0227_complycube_client_risk.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP TABLE IF EXISTS compliance.client_risk;
        """
    )
