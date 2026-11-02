"""view-as sessions: a member of staff opening the client portal as one of their clients

Revision ID: 0232_view_as_sessions
Revises: 0231_integrations_schema
Create Date: 2026-10-05

``alembic_version.version_num`` is ``varchar(32)``; this id is 21 characters.
"""

from pathlib import Path

from alembic import op

revision: str = "0232_view_as_sessions"
down_revision: str | None = "0231_integrations_schema"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0232_view_as_sessions.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The two policies go back to their 0002 shape (the adviser-self branch unguarded).
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP POLICY IF EXISTS contact_users_read ON crm.contact_users;
        CREATE POLICY contact_users_read ON crm.contact_users FOR SELECT TO authenticated USING (
          user_id = auth.uid()
          OR (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
          OR contact_id = ANY (auth.contact_scope())
        );
        DROP POLICY IF EXISTS client_advisers_read ON crm.client_advisers;
        CREATE POLICY client_advisers_read ON crm.client_advisers FOR SELECT TO authenticated USING (
          adviser_id = auth.uid()
          OR (auth.can_read_all() AND tenant_id = ANY (auth.tenant_lineage()))
          OR contact_id = ANY (auth.contact_scope())
        );
        DROP FUNCTION IF EXISTS auth.view_as();
        DROP FUNCTION IF EXISTS auth.resolve_view_as_claims(text, text, int);
        DROP TABLE IF EXISTS auth.view_as_sessions;
        """
    )
