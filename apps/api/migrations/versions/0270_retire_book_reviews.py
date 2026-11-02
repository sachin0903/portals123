"""retire four book reviews; model and fee reviews fold into their provider rows

Revision ID: 0270_retire_book_reviews
Revises: 0269_merge_sender_domains
Create Date: 2026-10-22

Data only. ``instruction.raised`` / ``instruction.cancelled`` / ``account.regular_movements_changed``
/ ``account.beneficiaries_changed`` leave the review catalogue: a money instruction is held at its
SEND by the money action's approvers, a cancel is immediate, a schedule is ``payment.regular`` /
``withdrawal.regular``, a nomination is ``beneficiary.replace``. Any hold still open on those keys
is closed as cancelled (there is no apply left to run it), and their firm policies go.

``account.model_changed`` / ``account.fee_packages_changed`` stay as the keys the doors hold on,
but the settings row a firm configures is now ``model.attach`` ("Change model") /
``fee.attach`` ("Change fee packages") — one row that holds the save and pushes to the custodian.
What the book row carries is carried onto the provider row. EXPAND ONLY: the book rows are not
deleted here — this migration runs before the new release is live, and the running release
still holds on them — the first save of the provider row retires its pair
(``actions/runs.py::set_policy``). So a rollback keeps every firm's four-eye on model and fees.
"""

from pathlib import Path

from alembic import op

revision: str = "0270_retire_book_reviews"
down_revision: str | None = "0269_merge_sender_domains"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0270_retire_book_reviews.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    """Nothing to undo: the retired holds were closed and the provider rows only gained what
    the book rows (still present) carry. The catalogue entries are code."""
