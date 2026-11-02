"""contact-scoped KYC store (crm.kyc_checks) — identity/AML reused per person with validity

Revision ID: 0026_kyc_checks
Revises: 0025_flows_orchestration
Create Date: 2026-07-23
"""

from pathlib import Path

from alembic import op

revision: str = "0026_kyc_checks"
down_revision: str | None = "0025_flows_orchestration"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0026_kyc_checks.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS crm.kyc_checks CASCADE")
