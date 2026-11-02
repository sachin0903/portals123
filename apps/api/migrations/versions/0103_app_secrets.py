"""per-application client secrets: a portal authenticates with its own application's secret

Revision ID: 0103_app_secrets
Revises: 0098_resolve_claims_app_only
Create Date: 2026-08-19

Each WorkOS application has its own client secret; signing in through a portal's application needs
THAT secret (the env key fails invalid_client). Adds the three Fernet-encrypted app-secret columns.
"""

from pathlib import Path

from alembic import op

revision: str = "0103_app_secrets"
down_revision: str | None = "0102_operations_host"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0103_app_secrets.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE auth.environment_secrets "
        "  DROP COLUMN IF EXISTS client_portal_secret_ct, "
        "  DROP COLUMN IF EXISTS platform_secret_ct, "
        "  DROP COLUMN IF EXISTS operations_secret_ct;"
    )
