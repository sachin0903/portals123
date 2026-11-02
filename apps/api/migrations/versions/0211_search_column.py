"""Spotlight search: one stored `_search` column per table, trigram-indexed.

Revision ID: 0211_search_column
Revises: 0210_employer_facts_backfill
Create Date: 2026-09-30
"""

from pathlib import Path

from alembic import op

revision: str = "0211_search_column"
down_revision: str | None = "0210_employer_facts_backfill"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# (table, new index, partial predicate, 0182 expression index it replaces, that index's fields).
# The partial predicates are 0182's, so each index still covers exactly the rows search reads.
_TABLES: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "crm.contacts",
        "contacts_search_trgm",
        "WHERE deleted_at IS NULL",
        "contacts_spotlight_trgm",
        "uid, name, first_name, middle_name, surname, email, "
        "phone, mobile_phone, phone_numbers::text, national_identifier",
    ),
    (
        "platform.accounts",
        "accounts_search_trgm",
        "",
        "accounts_spotlight_trgm",
        "uid, name, platform_identifier, currency",
    ),
    (
        "platform.products",
        "products_search_trgm",
        "",
        "products_spotlight_trgm",
        "uid, code, name",
    ),
    (
        "documents.documents",
        "documents_search_trgm",
        "WHERE status <> 'archived'",
        "documents_spotlight_trgm",
        "uid, name, document_type, source",
    ),
    (
        "tasks.tasks",
        "tasks_search_trgm",
        "",
        "tasks_spotlight_trgm",
        "uid, title, category, subject_id, source_ref",
    ),
    (
        "flows.executions",
        "executions_search_trgm",
        "",
        "executions_spotlight_trgm",
        "uid, subject_type, subject_id::text",
    ),
    (
        "flows.flows",
        "flows_search_trgm",
        "",
        "flows_spotlight_trgm",
        "uid, code, name, description",
    ),
    (
        "messaging.groups",
        "groups_search_trgm",
        "",
        "groups_spotlight_trgm",
        "uid, name",
    ),
    (
        "reviews.requests",
        "reviews_search_trgm",
        "",
        "reviews_spotlight_trgm",
        "uid, case_no::text, action_key, event_type, subject_type, subject_id",
    ),
    (
        "auth.tenants",
        "tenants_search_trgm",
        "WHERE NOT is_model",
        "tenants_spotlight_trgm",
        "uid, name, alias, website",
    ),
)


def _schema(table: str) -> str:
    return table.split(".", 1)[0]


def upgrade() -> None:
    sql = (_SQL_DIR / "0211_search_column.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql.replace("%", "%%"))
    # CONCURRENTLY cannot run in a transaction: autocommit_block commits the new columns first.
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        for table, index, where, old_index, _fields in _TABLES:
            schema = _schema(table)
            # Drop first so a retry heals an INVALID shell left by a cancelled concurrent build.
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {schema}.{index}")
            op.execute(
                f"CREATE INDEX CONCURRENTLY {index} "
                f"ON {table} USING gin (_search gin_trgm_ops) {where}".rstrip()
            )
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {schema}.{old_index}")
        op.execute("SET lock_timeout = DEFAULT")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("SET lock_timeout = '5s'")
        for table, index, where, old_index, fields in reversed(_TABLES):
            schema = _schema(table)
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {schema}.{old_index}")
            op.execute(
                f"CREATE INDEX CONCURRENTLY {old_index} "
                f"ON {table} USING gin (search.normalise({fields}) gin_trgm_ops) {where}".rstrip()
            )
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {schema}.{index}")
        op.execute("SET lock_timeout = DEFAULT")
    for table, *_rest in reversed(_TABLES):
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS _search")
    op.execute("DROP FUNCTION IF EXISTS search.phone_text(jsonb)")
