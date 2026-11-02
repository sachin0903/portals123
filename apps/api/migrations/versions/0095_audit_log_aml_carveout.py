"""audit.log -- exclude aml.* rows from the authenticated read policy (tipping-off defence-in-depth)

Revision ID: 0095_audit_aml_carveout
Revises: 0094_docs_status_cleanup
Create Date: 2026-08-13

Adds the structural tipping-off control the action stream already has (FIRM_EXCLUDED_EVENT_PREFIXES)
to the DML trail: an `authenticated` firm principal can no longer read an aml.* audit.log row (the
raw screening before/after). Ops still reads it via /admin/audit (service_role, BYPASSRLS). DDL in
the sibling SQL; downgrade restores the pre-carve-out policy.
"""

from pathlib import Path

from alembic import op

revision: str = "0095_audit_aml_carveout"
down_revision: str | None = "0094_docs_status_cleanup"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0095_audit_log_aml_carveout.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER POLICY audit_log_read ON audit.log USING ("
        "  tenant_id = ANY (auth.tenant_lineage()) "
        "  OR (tenant_id IS NULL AND auth.is_system()))"
    )
