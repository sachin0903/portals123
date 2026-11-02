"""Drop the reviews:* permissions

Revision ID: 0255_drop_review_permissions
Revises: 0254_merge_aml_and_reason
Create Date: 2026-10-15

A held action is decided by the roles named on that action. An empty invoke
list means anyone may run it, and an empty approver list means no second
person is required. ``reviews:read``, ``reviews:write`` and ``reviews:approve``
were a second grant on top of that, so they go. Role grants cascade with the
permission row.

``auth.permissions`` is FORCE ROW LEVEL SECURITY and the migrator is not
BYPASSRLS. A delete as the owner matches nothing and the revision still
commits (the 0202 trap). ``service_role`` bypasses that. The check after the
delete fails the migration if a row is still there.

``alembic_version.version_num`` is ``varchar(32)``; this id is 28 characters.
"""

from alembic import op

revision: str = "0255_drop_review_permissions"
down_revision: str | None = "0254_merge_aml_and_reason"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        SET LOCAL ROLE service_role;
        DELETE FROM auth.permissions
         WHERE slug IN ('reviews:read', 'reviews:write', 'reviews:approve');
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM auth.permissions
             WHERE slug IN ('reviews:read', 'reviews:write', 'reviews:approve')
          ) THEN
            RAISE EXCEPTION 'reviews permissions were not removed';
          END IF;
        END $$;
        RESET ROLE;
        """
    )


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        SET LOCAL ROLE service_role;
        INSERT INTO auth.permissions (slug, name, resource, action, portal, description)
        VALUES
          ('reviews:read', 'View peer review', 'reviews', 'read', 'platform',
           'View the firm''s peer-review queue and pending four-eye checks.'),
          ('reviews:write', 'Manage peer review', 'reviews', 'write', 'platform',
           'Configure which actions require a four-eye check and which role reviews them.'),
          ('reviews:approve', 'Approve peer reviews', 'reviews', 'approve', 'platform',
           'Approve or reject a held action. The proposer cannot approve their own.')
        ON CONFLICT (slug) DO NOTHING;
        RESET ROLE;
        """
    )
