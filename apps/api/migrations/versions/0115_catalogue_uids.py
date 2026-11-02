"""the last thirteen tables a URL names get a uid

Revision ID: 0115_catalogue_uids
Revises: 0114_api_log_uid
Create Date: 2026-08-26

Every serial still in an API path belonged to a table with no `uid`: the firm-configured catalogue,
the provider and platform registries, and four singletons. Added NULLable-then-defaulted rather
than `NOT NULL DEFAULT` in one step — nanoid is volatile, so the one-step form rewrites the table
under an exclusive lock.
"""

from pathlib import Path

from alembic import op

revision: str = "0115_catalogue_uids"
down_revision: str | None = "0114_api_log_uid"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

#: The tables this adds a uid to, so `downgrade` can take it away again.
_TABLES: tuple[str, ...] = (
    "platform.models",
    "platform.products",
    "platform.model_versions",
    "platform.model_commentary",
    "platform.fee_packages",
    "platform.platforms",
    "platform.third_party_providers",
    "platform.transfer_providers",
    "auth.tenant_environments",
    "storage.user_credentials",
    "documents.document_types",
    "flows.outbox",
    "documents.render_jobs",
)


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0115_catalogue_uids.sql")


def downgrade() -> None:
    for table in _TABLES:
        op.get_bind().exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN IF EXISTS uid")
