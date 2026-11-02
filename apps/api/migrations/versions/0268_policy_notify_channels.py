"""per-policy notification channels for approval notices (in-app and/or email)

Revision ID: 0268_policy_notify_channels
Revises: 0267_product_wrapper_ref
Create Date: 2026-10-21

Adds actions.policies.notify_in_app / notify_email. Approvers of a held run were notified both
in-app and by email; a firm can now choose the channels per action policy, defaulting to in-app
only. The channels ride the review.requested event and are honoured by the two notification
consumers.
"""

from pathlib import Path

from alembic import op

revision: str = "0268_policy_notify_channels"
down_revision: str | None = "0267_product_wrapper_ref"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0268_policy_notify_channels.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "ALTER TABLE actions.policies DROP COLUMN notify_in_app, DROP COLUMN notify_email;"
    )
