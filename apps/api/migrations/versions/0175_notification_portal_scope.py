"""A notification is readable only from the portal it was raised for

Revision ID: 0175_notification_portal_scope
Revises: 0174_product_currencies
Create Date: 2026-09-16
"""

from pathlib import Path

from alembic import op

revision: str = "0175_notification_portal_scope"
down_revision: str | None = "0174_product_currencies"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0175_notification_portal_scope.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Back to 0067's pair verbatim — the staff/client split without the portal predicate. Written
    # out rather than re-reading 0067.sql, so a later edit to that file cannot silently change what
    # this downgrade restores.
    op.get_bind().exec_driver_sql(
        """
        DROP INDEX IF EXISTS notifications.notifications_user_portal_idx;

        DROP POLICY IF EXISTS notifications_read ON notifications.notifications;
        CREATE POLICY notifications_read ON notifications.notifications
          FOR SELECT TO authenticated USING (
            (auth.tenant_lineage() <> '{}'::int[] AND recipient_user_id = auth.uid())
            OR (auth.tenant_lineage() = '{}'::int[]
                AND recipient_contact_id = ANY (auth.contact_scope())));

        DROP POLICY IF EXISTS notifications_mark ON notifications.notifications;
        CREATE POLICY notifications_mark ON notifications.notifications
          FOR UPDATE TO authenticated
          USING (
            (auth.tenant_lineage() <> '{}'::int[] AND recipient_user_id = auth.uid())
            OR (auth.tenant_lineage() = '{}'::int[]
                AND recipient_contact_id = ANY (auth.contact_scope())))
          WITH CHECK (
            (auth.tenant_lineage() <> '{}'::int[] AND recipient_user_id = auth.uid())
            OR (auth.tenant_lineage() = '{}'::int[]
                AND recipient_contact_id = ANY (auth.contact_scope())));
        """
    )
