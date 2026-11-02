"""auth.users: store the email folded, and split is_adviser out of type

Revision ID: 0137_folded_email_and_adviser
Revises: 0136_provider_users
Create Date: 2026-09-07
"""

from pathlib import Path

from alembic import op

revision: str = "0137_folded_email_and_adviser"
down_revision: str | None = "0136_provider_users"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    name = "0137_folded_email_and_adviser.sql"
    sql = (_SQL_DIR / name).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS users_tenant_email_ci_key
          ON auth.users (tenant_id, lower(email));
        ALTER TABLE auth.users
          DROP CONSTRAINT IF EXISTS users_email_is_folded,
          DROP CONSTRAINT IF EXISTS users_adviser_is_staff,
          DROP CONSTRAINT IF EXISTS users_type_members;
        UPDATE auth.users SET type = type || ARRAY['adviser'] WHERE is_adviser;
        ALTER TABLE auth.users
          ADD CONSTRAINT users_type_members
          CHECK (type <@ ARRAY['client', 'staff', 'adviser']::text[]);
        ALTER TABLE auth.users DROP COLUMN IF EXISTS is_adviser;
        """
    )
