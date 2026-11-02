"""Join main's sender-domains line to the baton line

Revision ID: 0269_merge_sender_domains
Revises: 0268_policy_notify_channels, 0264_sender_domains
Create Date: 2026-10-22

NO DDL. Like 0244 and 0247, this revision exists only to give Alembic one head again.

Two lineages branch off ``0263_client_groups``:

    0263 -> 0261_baton -> 0262_drop_legacy_actions -> 0263_transaction_money_namespace
         -> 0264_withdrawal_fee_disinvest -> 0265_adviser_fee_type -> 0266_action_policy_manual
         -> 0267_product_wrapper_ref -> 0268_policy_notify_channels
    0263 -> 0264_sender_domains

The baton branch left ``0263_client_groups`` before main took ``0264_sender_domains`` (a firm's
own verified sending domain), so rebasing the branch onto main produced two heads. Nothing in
either line touches the other's tables.

``downgrade`` is a no-op for the same reason ``upgrade`` is: unmerging would restore two heads.

``alembic_version.version_num`` is ``varchar(32)``; this id is 25 characters.
"""

from collections.abc import Sequence

revision: str = "0269_merge_sender_domains"
down_revision: str | Sequence[str] | None = (
    "0268_policy_notify_channels",
    "0264_sender_domains",
)
_ = (revision, down_revision)  # Alembic reads these names off the module


def upgrade() -> None:
    """Nothing to do — this revision only joins two lineages."""


def downgrade() -> None:
    """Nothing to undo. Splitting the lineages again would restore the multiple-heads error."""
