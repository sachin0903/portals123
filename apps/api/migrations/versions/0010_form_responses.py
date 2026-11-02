"""persist completed forms: request_items.form_uid + documents.form_responses

Revision ID: 0010_form_responses
Revises: 0009_request_action_agree
Create Date: 2026-07-10

Adds a form_uid link on request_items and a documents.form_responses table (answers stored as a jsonb
blob keyed by fieldKey — the shape DynamicForm.collect() produces). DDL lives in the sibling SQL file;
this script applies it as the elevated migrator role. Bound to auth.users for now (interim) — see the
SQL header.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_form_responses"
down_revision: str | None = "0009_request_action_agree"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0010_form_responses.sql")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS documents.form_responses CASCADE")
    op.execute("ALTER TABLE documents.request_items DROP COLUMN IF EXISTS form_uid")
