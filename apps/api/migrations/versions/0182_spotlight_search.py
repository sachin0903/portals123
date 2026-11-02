"""Spotlight search: shared normaliser and concurrent text indexes.

Revision ID: 0182_spotlight_search
Revises: 0181_client_classifications
Create Date: 2026-09-23
"""

from pathlib import Path

from alembic import op

revision: str = "0182_spotlight_search"
down_revision: str | None = "0181_client_classifications"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "crm.contacts_spotlight_trgm",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS contacts_spotlight_trgm
           ON crm.contacts USING gin (
             search.normalise(
               uid, name, first_name, middle_name, surname, email,
               phone, mobile_phone, phone_numbers::text, national_identifier
             ) gin_trgm_ops
           ) WHERE deleted_at IS NULL""",
    ),
    (
        "platform.accounts_spotlight_trgm",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS accounts_spotlight_trgm
           ON platform.accounts USING gin (
             search.normalise(uid, name, platform_identifier, currency) gin_trgm_ops
           )""",
    ),
    (
        "platform.products_spotlight_trgm",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS products_spotlight_trgm
           ON platform.products USING gin (
             search.normalise(uid, code, name) gin_trgm_ops
           )""",
    ),
    (
        "platform.transactions_spotlight_trgm",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS transactions_spotlight_trgm
           ON platform.transactions USING gin (
             search.normalise(
               source_transaction_id, source_type, source_subtype,
               isin, asset_name, currency, status
             ) gin_trgm_ops
           )""",
    ),
    (
        "documents.documents_spotlight_trgm",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS documents_spotlight_trgm
           ON documents.documents USING gin (
             search.normalise(uid, name, document_type, source) gin_trgm_ops
           ) WHERE status <> 'archived'""",
    ),
    (
        "tasks.tasks_spotlight_trgm",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS tasks_spotlight_trgm
           ON tasks.tasks USING gin (
             search.normalise(uid, title, category, subject_id, source_ref) gin_trgm_ops
           )""",
    ),
    (
        "tasks.tasks_spotlight_fts",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS tasks_spotlight_fts
           ON tasks.tasks USING gin (
             to_tsvector('simple', coalesce(description, ''))
           )""",
    ),
    (
        "flows.executions_spotlight_trgm",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS executions_spotlight_trgm
           ON flows.executions USING gin (
             search.normalise(uid, subject_type, subject_id::text) gin_trgm_ops
           )""",
    ),
    (
        "flows.flows_spotlight_trgm",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS flows_spotlight_trgm
           ON flows.flows USING gin (
             search.normalise(uid, code, name, description) gin_trgm_ops
           )""",
    ),
    (
        "messaging.messages_spotlight_fts",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS messages_spotlight_fts
           ON messaging.messages USING gin (
             to_tsvector('simple', coalesce(body, ''))
           )""",
    ),
    (
        "messaging.groups_spotlight_trgm",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS groups_spotlight_trgm
           ON messaging.groups USING gin (
             search.normalise(uid, name) gin_trgm_ops
           )""",
    ),
    (
        "reviews.reviews_spotlight_trgm",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS reviews_spotlight_trgm
           ON reviews.requests USING gin (
             search.normalise(
               uid, case_no::text, action_key, event_type, subject_type, subject_id
             ) gin_trgm_ops
           )""",
    ),
    (
        "reviews.reviews_spotlight_fts",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS reviews_spotlight_fts
           ON reviews.requests USING gin (
             to_tsvector(
               'simple',
               coalesce(justification, '') || ' ' || coalesce(decision_note, '')
             )
           )""",
    ),
    (
        "auth.tenants_spotlight_trgm",
        """CREATE INDEX CONCURRENTLY IF NOT EXISTS tenants_spotlight_trgm
           ON auth.tenants USING gin (
             search.normalise(uid, name, alias, website) gin_trgm_ops
           ) WHERE NOT is_model""",
    ),
)


def upgrade() -> None:
    sql = (_SQL_DIR / "0182_spotlight_search.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql.replace("%", "%%"))
    # PostgreSQL refuses CONCURRENTLY inside a transaction. autocommit_block commits the
    # extension/function expansion first, then gives each index its non-blocking build.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        for name, statement in _INDEXES:
            # A cancelled concurrent build leaves an INVALID index with the same name; IF NOT
            # EXISTS would then preserve that unusable shell forever. Drop first so a retry heals.
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(statement)
        op.execute("SET lock_timeout = DEFAULT")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        for name, _statement in reversed(_INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
        op.execute("SET lock_timeout = DEFAULT")
    op.execute("DROP FUNCTION IF EXISTS search.has_prefix(text, VARIADIC text[])")
    op.execute("DROP FUNCTION IF EXISTS search.normalise(VARIADIC text[])")
    op.execute("DROP SCHEMA IF EXISTS search")
    # pg_trgm may be shared by operator-owned indexes, so a feature downgrade must not drop it.
