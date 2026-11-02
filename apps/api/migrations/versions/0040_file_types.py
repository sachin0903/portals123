"""supporting-file types in the document-type registry (kind discriminator)

Revision ID: 0040_file_types
Revises: 0039_document_types
Create Date: 2026-07-30

Adds a `kind` column ('document' | 'file') to documents.document_types so the per-tenant registry
manages BOTH document types and supporting-file types (was a hardcoded frontend list). Seeds the
default supporting-file types for existing tenants and updates the new-tenant seed trigger to seed
both. DDL in the sibling SQL file; applied as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0040_file_types"
down_revision: str | None = "0039_document_types"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0040_file_types.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        # Restore the document-only seed trigger, drop the seeded file types, and remove the column
        # + per-kind key (restoring the original per-slug key).
        "CREATE OR REPLACE FUNCTION documents.seed_default_document_types() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "INSERT INTO documents.document_types (tenant_id, slug, label, sort_order) VALUES "
        "(NEW.id, 'report', 'Report', 1), (NEW.id, 'statement', 'Statement', 2), "
        "(NEW.id, 'agreement', 'Agreement', 3), (NEW.id, 'identity', 'Identity', 4), "
        "(NEW.id, 'tax', 'Tax', 5), (NEW.id, 'illustration', 'Illustration', 7), "
        "(NEW.id, 'application', 'Application', 8), (NEW.id, 'letter', 'Letter', 9) "
        "ON CONFLICT (tenant_id, slug) DO NOTHING; RETURN NEW; END; $$;"
        "DELETE FROM documents.document_types WHERE kind = 'file';"
        "ALTER TABLE documents.document_types DROP CONSTRAINT document_types_tenant_kind_slug_key;"
        "ALTER TABLE documents.document_types "
        "  ADD CONSTRAINT document_types_tenant_slug_key UNIQUE (tenant_id, slug);"
        "ALTER TABLE documents.document_types DROP COLUMN kind;"
    )
