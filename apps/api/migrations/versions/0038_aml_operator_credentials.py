"""aml phase 2: per-operator Creditsafe credentials, and ops-visible unattributed profiles

Revision ID: 0038_aml_operator_credentials
Revises: 0037_aml_creditsafe
Create Date: 2026-07-29

The DDL lives in the sibling SQL file (migrations/sql/0038_aml_operator_credentials.sql) --
that file is the reviewable artifact; this script just applies it. Runs as the elevated
migrator role (see migrations/env.py).
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0038_aml_operator_credentials"
down_revision: str | None = "0037_aml_creditsafe"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    # exec_driver_sql sends the literal SQL straight to psycopg (no SQLAlchemy bind-param
    # parsing, so `::cast` is safe) as a single multi-statement script. psycopg uses
    # pyformat, so it still scans for `%` placeholders -- double every `%` (in RAISE/
    # format() bodies) so psycopg collapses `%%` back to a literal `%` for the server.
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0038_aml_operator_credentials.sql")


def downgrade() -> None:
    # Drop the credential tables and put the mirror's NOT NULLs back. Restoring them requires
    # clearing unattributed rows first -- they exist only because the column became nullable,
    # and the mirror is a disposable read model that a re-sync rebuilds.
    op.execute("DROP TABLE IF EXISTS aml.operator_secrets")
    op.execute("DROP TABLE IF EXISTS aml.operator_credentials")
    op.execute("DELETE FROM aml.hits WHERE tenant_id IS NULL")
    op.execute("DELETE FROM aml.searches WHERE tenant_id IS NULL")
    op.execute("DELETE FROM aml.profiles WHERE tenant_id IS NULL")
    op.execute("DROP INDEX IF EXISTS aml.aml_profiles_unattributed_idx")
    op.execute("ALTER TABLE aml.profiles DROP COLUMN IF EXISTS linked_at")
    op.execute("ALTER TABLE aml.profiles DROP COLUMN IF EXISTS linked_by")
    for table in ("profiles", "searches", "hits"):
        op.execute(f"DROP POLICY IF EXISTS {table}_read ON aml.{table}")
        op.execute(
            f"CREATE POLICY {table}_read ON aml.{table} FOR SELECT TO authenticated "
            "USING (tenant_id = ANY (auth.tenant_lineage()))"
        )
        op.execute(f"ALTER TABLE aml.{table} ALTER COLUMN tenant_id SET NOT NULL")
