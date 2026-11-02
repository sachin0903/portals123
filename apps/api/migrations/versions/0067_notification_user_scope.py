"""notification user-scope -- staff notifications never show on the client portal (0066's twin)

Revision ID: 0067_notification_user_scope
Revises: 0066_notification_rls_scope
Create Date: 2026-08-06

0066 gated the contact branch to client principals; this gates the USER branch to STAFF principals
(non-empty auth.tenant_lineage()), so a staff user's own notifications never surface in the client
portal (the leak: a client session whose auth.uid() coincides with a staff user id saw that user's
"task assigned" notices). DDL in the sibling SQL file; recreates the read + mark policies.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0067_notification_user_scope"
down_revision: str | None = "0066_notification_rls_scope"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

# Restore the 0066 policies (user branch ungated) on downgrade.
_DOWN = """
DROP POLICY IF EXISTS notifications_read ON notifications.notifications;
CREATE POLICY notifications_read ON notifications.notifications FOR SELECT TO authenticated USING (
  recipient_user_id = auth.uid()
  OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope())));
DROP POLICY IF EXISTS notifications_mark ON notifications.notifications;
CREATE POLICY notifications_mark ON notifications.notifications FOR UPDATE TO authenticated
  USING (
    recipient_user_id = auth.uid()
    OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope())))
  WITH CHECK (
    recipient_user_id = auth.uid()
    OR (auth.tenant_lineage() = '{}'::int[] AND recipient_contact_id = ANY (auth.contact_scope())));
"""


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0067_notification_user_scope.sql")


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN.replace("%", "%%"))
