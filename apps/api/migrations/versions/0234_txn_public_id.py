"""An outbound transaction's reference is the origin's public id.

Revision ID: 0234_txn_public_id
Revises: 0233_txn_tenant_key
Create Date: 2026-10-06

``alembic_version.version_num`` is ``varchar(32)``; this id is 19 characters.

``gxn:{origin}:{uid}`` was a stand-in. ``source_system = graphene`` already keeps the row
off the custodian feed, and ``data.origin`` names which record it belongs to. The prefix
was also how 0197 recognised an outbound row, because ``ADD VALUE`` cannot be used in the
same transaction that adds an enum member. ``graphene`` is committed now, so the check and
the index key off ``source_system``.
"""

from alembic import op

revision: str = "0234_txn_public_id"
down_revision: str | None = "0233_txn_tenant_key"
_ = (revision, down_revision)  # Alembic reads these names off the module

_CHECK = """
ALTER TABLE platform.transactions
  ADD CONSTRAINT transactions_outbound_status CHECK (
    source_system <> 'graphene'
    OR (
      status IN ('pending', 'submitted', 'posted', 'failed', 'cancelled')
      AND (data ->> 'origin') IN ('instruction', 'contribution', 'transfer')
      AND NULLIF(data ->> 'origin_uid', '') IS NOT NULL
    )
  )
"""

_OLD_CHECK = """
ALTER TABLE platform.transactions
  ADD CONSTRAINT transactions_outbound_status CHECK (
    source_transaction_id NOT LIKE 'gxn:%'
    OR (
      status IN ('pending', 'submitted', 'posted', 'failed', 'cancelled')
      AND (data ->> 'origin') IN ('instruction', 'contribution', 'transfer')
      AND NULLIF(data ->> 'origin_uid', '') IS NOT NULL
    )
  )
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
              FROM platform.transactions
             WHERE source_system = 'graphene'
               AND source_transaction_id LIKE 'gxn:%'
               AND NULLIF(data ->> 'origin_uid', '') IS NOT NULL
             GROUP BY tenant_id, data ->> 'origin_uid'
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION 'two graphene rows would share one public id';
          END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE platform.transactions DROP CONSTRAINT IF EXISTS transactions_outbound_status"
    )
    op.execute(_CHECK)
    op.execute(
        "COMMENT ON CONSTRAINT transactions_outbound_status ON platform.transactions IS "
        "'Graphene-originated rows (source_system = graphene). The reference is the origin "
        "public id. Inbound feed status is unconstrained vendor text.'"
    )
    op.execute(
        "COMMENT ON COLUMN platform.transactions.status IS "
        "'Inbound: the custodian''s own status string. Outbound (source_system = graphene): "
        "pending (local, not yet accepted) | submitted (vendor API accepted) | posted "
        "(matched to a landed credit) | failed (vendor refused) | cancelled (local cancel "
        "before a posted credit).'"
    )
    op.execute("DROP INDEX IF EXISTS platform.transactions_outbound_origin_idx")
    op.execute(
        "CREATE INDEX transactions_outbound_origin_idx "
        "ON platform.transactions ((data ->> 'origin'), (data ->> 'origin_uid')) "
        "WHERE source_system = 'graphene'"
    )
    op.execute(
        """
        UPDATE platform.transactions
           SET source_transaction_id = data ->> 'origin_uid'
         WHERE source_system = 'graphene'
           AND source_transaction_id LIKE 'gxn:%'
           AND NULLIF(data ->> 'origin_uid', '') IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        UPDATE platform.transactions
           SET source_transaction_id =
                 'gxn:' || (data ->> 'origin') || ':' || (data ->> 'origin_uid')
         WHERE source_system = 'graphene'
           AND source_transaction_id NOT LIKE 'gxn:%'
           AND (data ->> 'origin') IN ('instruction', 'contribution', 'transfer')
           AND NULLIF(data ->> 'origin_uid', '') IS NOT NULL
        """
    )
    op.execute(
        "ALTER TABLE platform.transactions DROP CONSTRAINT IF EXISTS transactions_outbound_status"
    )
    op.execute(_OLD_CHECK)
    op.execute("DROP INDEX IF EXISTS platform.transactions_outbound_origin_idx")
    op.execute(
        "CREATE INDEX transactions_outbound_origin_idx "
        "ON platform.transactions ((data ->> 'origin_uid')) "
        "WHERE source_transaction_id LIKE 'gxn:%'"
    )
