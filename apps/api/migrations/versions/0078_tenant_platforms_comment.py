"""Re-point platform.tenant_platforms' COMMENT at code that exists

Revision ID: 0078_tenant_platforms_comment
Revises: 0077_drop_provider_instructions
Create Date: 2026-08-10

Comment-only; no DDL, no data. 0048's comment named `app/fanout.py`, which has been deleted, and a
table comment is the one piece of documentation that ships inside the database and that no gate
ever reads.
"""

from pathlib import Path

from alembic import op

revision: str = "0078_tenant_platforms_comment"
down_revision: str | None = "0077_drop_provider_instructions"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0078_tenant_platforms_comment.sql")


def downgrade() -> None:
    # 0048's wording, restored verbatim.
    op.get_bind().exec_driver_sql(
        "COMMENT ON TABLE platform.tenant_platforms IS "
        "'Which providers a tenant is enabled on, and the slice of the provider''s data that is "
        "theirs. See app/fanout.py and docs/provider-routing.md.'"
    )
