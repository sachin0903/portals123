"""documents -- retire the dead 'superseded'/'generating' status + the superseded_by column

Revision ID: 0094_docs_status_cleanup
Revises: 0093_member_permissions
Create Date: 2026-08-13

Removes the unbuilt document-versioning footprint: the `superseded_by` self-FK, the `superseded`
status value (never written; only ever excluded on read) and the never-written `generating` value.
DDL in the sibling SQL; the read filters that named 'superseded' are stripped in the same change.
Downgrade re-adds them (best-effort — the feature they belonged to was never built).
"""

from pathlib import Path

from alembic import op

revision: str = "0094_docs_status_cleanup"
down_revision: str | None = "0093_member_permissions"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0094_documents_status_cleanup.sql")


def downgrade() -> None:
    # Symmetric rebuild: restore the two enum values and the column (transaction-safe — the swap does
    # not USE the re-added values in the same statement).
    op.get_bind().exec_driver_sql(
        "ALTER TABLE documents.documents ALTER COLUMN status DROP DEFAULT; "
        "ALTER TYPE documents.document_status RENAME TO document_status_new; "
        "CREATE TYPE documents.document_status AS ENUM "
        "  ('queued', 'generating', 'generated', 'failed', 'superseded', 'archived'); "
        "ALTER TABLE documents.documents ALTER COLUMN status TYPE documents.document_status "
        "  USING status::text::documents.document_status; "
        "ALTER TABLE documents.documents ALTER COLUMN status SET DEFAULT 'queued'; "
        "DROP TYPE documents.document_status_new; "
        "ALTER TABLE documents.documents ADD COLUMN IF NOT EXISTS superseded_by uuid "
        "  REFERENCES documents.documents(id) ON DELETE SET NULL"
    )
