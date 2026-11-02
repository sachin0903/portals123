"""The signed copy behind an in-person terms acceptance

Revision ID: 0207_terms_acceptance_evidence
Revises: 0206_allowance_remaining
Create Date: 2026-09-29

An adviser recording a client's acceptance on their behalf must upload the signed terms. The
register row points at that file so the evidence is found from the acceptance, not by searching
the client's documents for something that looks like it.
"""

from pathlib import Path

from alembic import op

revision: str = "0207_terms_acceptance_evidence"
down_revision: str | None = "0206_allowance_remaining"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0207_terms_acceptance_evidence.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP INDEX IF EXISTS terms.acceptances_evidence_document_idx; "
        "ALTER TABLE terms.acceptances DROP COLUMN IF EXISTS evidence_document_id"
    )
