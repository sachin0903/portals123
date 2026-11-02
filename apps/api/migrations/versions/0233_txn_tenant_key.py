"""A transaction belongs to the firm that synced it.

Revision ID: 0233_txn_tenant_key
Revises: 0232_view_as_sessions
Create Date: 2026-10-06

``alembic_version.version_num`` is ``varchar(32)``; this id is 20 characters.

Two tenants may be configured with the same SEI ``firm_id``. Both then stage the same
provider transaction ids. The old key was ``(source_system, source_transaction_id)``, so
both merges wrote one row: the second overwrote the first firm's amount, and the two
upserts deadlocked when they locked that row in different orders.

The row is tenant data. The key is ``(tenant_id, source_system, source_transaction_id)``.
``tenant_id`` is already NOT NULL, so the wider key admits every existing row.
"""

from alembic import op

revision: str = "0233_txn_tenant_key"
down_revision: str | None = "0232_view_as_sessions"
_ = (revision, down_revision)  # Alembic reads these names off the module

# CREATE INDEX cannot take a schema. The index is created in `platform` because
# that is where the table is. DROP must name the schema: an unqualified name
# looks in `public`, and a retry then leaves the half-built index behind.
_NEW = "transactions_tenant_source_key"
_OLD = "transactions_source_unique"
_NEW_INDEX = f"platform.{_NEW}"
_OLD_INDEX = f"platform.{_OLD}"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_NEW_INDEX}")
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY transactions_tenant_source_key "
            "ON platform.transactions (tenant_id, source_system, source_transaction_id)"
        )
        op.execute(f"ALTER TABLE platform.transactions DROP CONSTRAINT IF EXISTS {_OLD}")
        op.execute(
            "ALTER TABLE platform.transactions "
            "ADD CONSTRAINT transactions_tenant_source_key "
            "UNIQUE USING INDEX transactions_tenant_source_key"
        )
        op.execute(
            "COMMENT ON CONSTRAINT transactions_tenant_source_key "
            "ON platform.transactions IS "
            "'One provider transaction per firm. Two tenants syncing the same custodian "
            "firm each keep a copy.'"
        )
        op.execute("SET lock_timeout = DEFAULT")


def downgrade() -> None:
    # Narrowing the key fails once two firms hold the same provider id. That is the case
    # this revision exists to allow.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        op.execute(
            "ALTER TABLE platform.transactions "
            "DROP CONSTRAINT IF EXISTS transactions_tenant_source_key"
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_OLD_INDEX}")
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY transactions_source_unique "
            "ON platform.transactions (source_system, source_transaction_id)"
        )
        op.execute(
            "ALTER TABLE platform.transactions "
            "ADD CONSTRAINT transactions_source_unique "
            "UNIQUE USING INDEX transactions_source_unique"
        )
        op.execute("SET lock_timeout = DEFAULT")
