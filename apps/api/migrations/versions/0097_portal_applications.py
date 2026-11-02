"""portal-via-application (P1): store each portal's WorkOS Application client id

Revision ID: 0097_portal_applications
Revises: 0092_rule_versions
Create Date: 2026-08-16

Step P1 of moving the portal boundary from WorkOS organisations to WorkOS applications
(docs/auth-applications-migration.md, path A1). Additive/expand-only: adds
auth.tenant_environments.{client_portal,platform,operations}_client_id alongside the existing
*_org_id columns. Nothing reads them yet, so there is no behaviour change; population is per firm as
its applications are created in the WorkOS dashboard (admin onboarding checklist, P6). DDL in the
sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0097_portal_applications"
down_revision: str | None = "0096_event_vocabulary"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0097_portal_applications.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE auth.tenant_environments "
        "  DROP COLUMN IF EXISTS client_portal_client_id, "
        "  DROP COLUMN IF EXISTS platform_client_id, "
        "  DROP COLUMN IF EXISTS operations_client_id;"
    )
