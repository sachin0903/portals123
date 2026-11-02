"""products: one shelf item across every platform

Revision ID: 0044_products_multi_platform
Revises: 0043_links_rename
Create Date: 2026-08-02

The DDL lives in the sibling SQL file (migrations/sql/0044_products_multi_platform.sql) --
that file is the reviewable artifact; this script just applies it. Runs as the elevated
migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0044_products_multi_platform"
down_revision: str | None = "0043_links_rename"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0044_products_multi_platform.sql")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS platform.products_tenant_type_key")
    op.execute("COMMENT ON COLUMN platform.products.platform_id IS NULL")
    # The NOT NULL cannot come back while tenant-scoped products legitimately carry no
    # platform, and the folded duplicates are gone for good. Adopting the first platform a
    # product is linked to is the closest honest reconstruction; a product linked to none is
    # left NULL and the constraint stays off rather than inventing a platform for it.
    op.execute(
        """
        UPDATE platform.products p
           SET platform_id = (SELECT pp.platform_id FROM platform.product_platforms pp
                               WHERE pp.product_id = p.id ORDER BY pp.platform_id LIMIT 1)
         WHERE p.platform_id IS NULL
        """
    )
