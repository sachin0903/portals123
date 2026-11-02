"""one-firm-org (A1): a single WorkOS org per firm, superseding the per-portal orgs

Revision ID: 0100_firm_org
Revises: 0095_portal_host_unique
Create Date: 2026-08-19

Additive: adds auth.tenant_environments.firm_org_id. Behaviour-preserving until ensure_firm_org
populates it and portal_org_and_client prefers it. DDL in the sibling SQL file.
"""

from pathlib import Path

from alembic import op

revision: str = "0100_firm_org"
down_revision: str | None = "0099_portal_host_unique"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0100_firm_org.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE auth.tenant_environments DROP COLUMN IF EXISTS firm_org_id;"
    )
