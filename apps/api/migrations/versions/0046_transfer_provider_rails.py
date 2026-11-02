"""transfer providers: rails, and keyed by the provider's own id

Revision ID: 0046_transfer_provider_rails
Revises: 0045_tenant_usage
Create Date: 2026-08-02

The DDL lives in the sibling SQL file -- that file is the reviewable artifact; this script just
applies it. Runs as the elevated migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0046_transfer_provider_rails"
down_revision: str | None = "0045_tenant_usage"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0046_transfer_provider_rails.sql")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS platform.transfer_providers_masterparty_key")
    op.execute("DROP INDEX IF EXISTS platform.transfer_providers_products_idx")
    # The name constraint cannot simply come back: the register legitimately holds two providers
    # of the same name (P1, reachable on Origo and on ATG), which is why it was dropped. Rows are
    # collapsed to one per name so the constraint can be restored without failing.
    op.execute("SET LOCAL ROLE service_role")
    op.execute(
        """
        DELETE FROM platform.transfer_providers p
         WHERE p.id <> (SELECT min(q.id) FROM platform.transfer_providers q
                         WHERE q.tenant_id = p.tenant_id AND q.name = p.name)
        """
    )
    op.execute("RESET ROLE")
    op.execute(
        "ALTER TABLE platform.transfer_providers "
        "ADD CONSTRAINT transfer_providers_name_unique UNIQUE (tenant_id, name)"
    )
    op.execute(
        "ALTER TABLE platform.transfer_providers "
        "DROP COLUMN products, DROP COLUMN rails, "
        "DROP COLUMN atg_reference, DROP COLUMN status"
    )
