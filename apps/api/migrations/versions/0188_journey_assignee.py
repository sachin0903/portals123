"""Journey assignee: the platform staff member who owns this journey's staff work.

Revision ID: 0188_journey_assignee
Revises: 0187_provider_columns
Create Date: 2026-09-25

Distinct from ``crm.applications.adviser_id``. The adviser is the person responsible
for the client, and must be marked an adviser. The assignee is whoever on the
platform side is doing the work — often admin staff — and is who staff tasks land on.
"""

from alembic import op

revision: str = "0188_journey_assignee"
down_revision: str | None = "0187_provider_columns"
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE flows.executions "
        "ADD COLUMN assignee_user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX executions_assignee_user_idx "
        "ON flows.executions (assignee_user_id) WHERE assignee_user_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP INDEX IF EXISTS flows.executions_assignee_user_idx")
    op.execute("ALTER TABLE flows.executions DROP COLUMN IF EXISTS assignee_user_id")
