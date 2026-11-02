"""platform.orgs becomes platform.nodes: lineage, type, and no is_tenant_root

Revision ID: 0146_orgs_are_nodes
Revises: 0145_one_provider_table
Create Date: 2026-09-08
"""

from pathlib import Path

from alembic import op

revision: str = "0146_orgs_are_nodes"
down_revision: str | None = "0145_one_provider_table"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    name = "0146_orgs_are_nodes.sql"
    sql = (_SQL_DIR / name).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The names come back; `is_tenant_root` comes back EMPTY, which is what it always was — it
    # was written by nothing, so `false` on every row IS the prior state.
    op.get_bind().exec_driver_sql("""
        ALTER TYPE platform.node_type RENAME TO org_type;

        ALTER POLICY nodes_read ON platform.nodes RENAME TO orgs_read;
        ALTER INDEX crm.contacts_node_id_idx        RENAME TO contacts_org_id_idx;
        ALTER INDEX platform.accounts_node_id_idx   RENAME TO accounts_org_id_idx;
        ALTER INDEX platform.nodes_lineage_idx      RENAME TO orgs_path_idx;
        ALTER INDEX platform.nodes_provider_key_idx RENAME TO orgs_provider_key_idx;
        ALTER INDEX platform.nodes_tenant_id_idx    RENAME TO orgs_tenant_id_idx;
        ALTER TABLE platform.nodes RENAME CONSTRAINT nodes_unique TO orgs_unique;

        ALTER TABLE crm.contacts     RENAME COLUMN node_id TO org_id;
        ALTER TABLE platform.accounts RENAME COLUMN node_id TO org_id;

        ALTER TABLE platform.nodes
          ADD COLUMN is_tenant_root boolean NOT NULL DEFAULT false;
        ALTER TABLE platform.nodes RENAME COLUMN type TO org_type;
        ALTER TABLE platform.nodes RENAME COLUMN lineage TO org_path;
        ALTER TABLE platform.nodes RENAME COLUMN key TO org_key;
        ALTER TABLE platform.nodes RENAME TO orgs;
    """)
