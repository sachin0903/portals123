"""platform.links: the key includes tenant_id, so a shared entity can be mapped by two firms

Revision ID: 0066_links_tenant_key
Revises: 0065_merge_linked_entity
Create Date: 2026-08-06

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0066_links_tenant_key"
down_revision: str | None = "0065_merge_linked_entity"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0066_links_tenant_key.sql")


def downgrade() -> None:
    # Narrowing the key back can fail on rows this migration made possible (two tenants mapping one
    # instrument), which is the point: the old key was the bug.
    op.execute(
        """
        SET LOCAL ROLE service_role;
        REVOKE UPDATE ON platform.tenant_platforms FROM book_writer;
        REVOKE SELECT ON storage.tenants FROM book_writer;
        REVOKE USAGE ON SCHEMA storage FROM book_writer;
        ALTER TABLE platform.links DROP CONSTRAINT links_unique_link;
        ALTER TABLE platform.links
          ADD CONSTRAINT links_unique_link UNIQUE (provider, entity_type, entity_id, link_type);
        RESET ROLE;
        """
    )
