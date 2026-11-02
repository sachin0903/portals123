"""links: platform.platform_links becomes platform.links

Revision ID: 0043_links_rename
Revises: 0042_platform_catalogue
Create Date: 2026-08-02

The DDL lives in the sibling SQL file (migrations/sql/0043_links_rename.sql) -- that file is
the reviewable artifact; this script just applies it. Runs as the elevated migrator role (see
migrations/env.py).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0043_links_rename"
down_revision: str | None = "0042_platform_catalogue"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0043_links_rename.sql")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS platform.entity_links")
    op.execute("ALTER POLICY links_read ON platform.links RENAME TO platform_links_read")
    op.execute("ALTER INDEX platform.links_tenant_id_idx RENAME TO platform_links_tenant_id_idx")
    op.execute(
        "ALTER INDEX platform.links_provider_external_idx "
        "RENAME TO platform_links_provider_external_idx"
    )
    op.execute("ALTER INDEX platform.links_entity_idx RENAME TO platform_links_entity_idx")
    op.execute(
        "ALTER TABLE platform.links RENAME CONSTRAINT links_unique_link "
        "TO platform_links_unique_link"
    )
    op.execute("ALTER TABLE platform.links RENAME TO platform_links")
    op.execute(
        """
        CREATE VIEW platform.entity_links AS
          SELECT tenant_id, entity_type, entity_id, provider, external_id, link_type, updated_at
          FROM platform.platform_links
          UNION ALL
          SELECT tenant_id, 'transaction', id, source_system, source_transaction_id, 'primary',
                 updated_at FROM platform.transactions
          UNION ALL
          SELECT tenant_id, 'valuation', id, source_system,
                 account_id || ':' || to_char(as_of_date, 'YYYY-MM-DD'), 'primary', updated_at
          FROM platform.valuations
        """
    )
    op.execute("GRANT SELECT ON platform.entity_links TO authenticated, service_role")
