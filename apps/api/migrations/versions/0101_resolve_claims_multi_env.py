"""resolve_claims: classify the portal across ALL of a tenant's environment rows (robust to dupes)

Revision ID: 0101_resolve_claims_multi_env
Revises: 0096_firm_org
Create Date: 2026-08-19

0094 read a single env row (lowest id) to classify the portal, so an app id saved to a different row
(e.g. the instance-stage row, or with a stale duplicate present) was never seen. This matches the
app/org ids across ANY of the tenant's env rows via EXISTS. CREATE OR REPLACE, same signature.
"""

from pathlib import Path

from alembic import op

revision: str = "0101_resolve_claims_multi_env"
down_revision: str | None = "0100_firm_org"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0101_resolve_claims_multi_env.sql")


def downgrade() -> None:
    # Restore the single-row classifier (0094's body).
    _run("0098_portal_from_application.sql")
