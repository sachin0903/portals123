"""notification RLS scope -- the contact branch is client-portal only (close a personal-feed leak)

Revision ID: 0066_notification_rls_scope
Revises: 0065_notification_tenant_config
Create Date: 2026-08-06

0063's policies let a staff adviser read/mark notifications addressed to their CLIENTS (their
contact_scope includes the book). A notification is personal, so the contact branch is gated on a
client-portal principal (empty auth.tenant_lineage(), the 0004 boundary). DDL in the sibling SQL
file; recreates the notifications read/mark + preferences policies.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0066_notification_rls_scope"
down_revision: str | None = "0065_notification_tenant_config"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# The 0063 policies this restores on downgrade (the leakier form).
_DOWN = """
DROP POLICY IF EXISTS notifications_read ON notifications.notifications;
CREATE POLICY notifications_read ON notifications.notifications FOR SELECT TO authenticated USING (
  recipient_user_id = auth.uid() OR recipient_contact_id = ANY (auth.contact_scope()));
DROP POLICY IF EXISTS notifications_mark ON notifications.notifications;
CREATE POLICY notifications_mark ON notifications.notifications FOR UPDATE TO authenticated
  USING     (recipient_user_id = auth.uid() OR recipient_contact_id = ANY (auth.contact_scope()))
  WITH CHECK (recipient_user_id = auth.uid() OR recipient_contact_id = ANY (auth.contact_scope()));
DROP POLICY IF EXISTS preferences_own ON notifications.preferences;
CREATE POLICY preferences_own ON notifications.preferences FOR ALL TO authenticated
  USING     (recipient_user_id = auth.uid() OR recipient_contact_id = ANY (auth.contact_scope()))
  WITH CHECK (recipient_user_id = auth.uid() OR recipient_contact_id = ANY (auth.contact_scope()));
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0066_notification_rls_scope.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
