"""Team/Profiles/Types split out of members:*/documents:admin, plus firm_settings:show

Revision ID: 0127_permission_split
Revises: 0125_content
Create Date: 2026-09-09
"""

from pathlib import Path

from alembic import op

revision: str = "0127_permission_split"
down_revision: str | None = "0125_content"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_NEW_SLUGS = (
    "firm_settings:show",
    "teams:read",
    "teams:write",
    "profiles:read",
    "profiles:write",
    "document_types:read",
    "document_types:write",
)


def upgrade() -> None:
    sql = (_SQL_DIR / "0127_permission_split.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # `slugs` is built from the fixed _NEW_SLUGS tuple above, never caller/request input.
    slugs = ", ".join(f"'{s}'" for s in _NEW_SLUGS)
    op.get_bind().exec_driver_sql(
        f"DELETE FROM auth.role_permissions WHERE permission_id IN "  # noqa: S608
        f"(SELECT id FROM auth.permissions WHERE slug IN ({slugs}));"
        f"DELETE FROM auth.permissions WHERE slug IN ({slugs});"
    )
