"""staff-authored dynamic forms: documents.forms + documents.form_fields

Revision ID: 0011_forms
Revises: 0010_form_responses
Create Date: 2026-07-13

Lands the persistence for the platform forms builder (docs/forms.md), previously frontend/mock only:
documents.forms (the form record) + documents.form_fields (its ordered field list), staff-only RLS
by tenant lineage. Also relaxes documents.form_responses to carry standalone (non-request) responses.
DDL lives in the sibling SQL file; this applies it as the elevated migrator role.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_forms"
down_revision: str | None = "0010_form_responses"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0011_forms.sql")


def downgrade() -> None:
    op.execute("ALTER TABLE documents.form_responses DROP COLUMN IF EXISTS form_id")
    # request_item_id was NOT NULL before 0008; a downgrade can only restore it if no standalone
    # (null) rows exist. Leave it nullable rather than fail — the column semantics are unchanged.
    op.execute("DROP TABLE IF EXISTS documents.form_fields CASCADE")
    op.execute("DROP TABLE IF EXISTS documents.forms CASCADE")
