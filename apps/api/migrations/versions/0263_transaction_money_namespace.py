"""move the one-off money actions into the transaction.* namespace; fold contribution.lodge

Revision ID: 0263_transaction_money_namespace
Revises: 0262_drop_legacy_actions
Create Date: 2026-10-18

Renames the firm-policy and baton run/subject action keys: payment.one_off -> transaction.payment,
withdrawal.one_off -> transaction.withdrawal, fee.adhoc -> transaction.fee, transfer.create ->
transaction.transfer. contribution.lodge's firm policy folds into transaction.payment (it is now the
internal lodge leaf transaction.payment calls for a contribution source; its key stays live for that
leaf's own runs). Downgrade reverses the four renames; the folded contribution.lodge policy cannot be
told apart from a native payment.one_off policy afterwards, so it is not split back out.
"""

from pathlib import Path

from alembic import op

revision: str = "0263_transaction_money_namespace"
down_revision: str | None = "0262_drop_legacy_actions"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0263_transaction_money_namespace.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # Reverse the four namespace renames. contribution.lodge stayed live, so only the renamed keys
    # move back; a transaction.payment policy that was folded in from contribution.lodge returns as
    # payment.one_off (indistinguishable from a native one by this point). Keys are fixed constants.
    renames = (
        ("transaction.payment", "payment.one_off"),
        ("transaction.withdrawal", "withdrawal.one_off"),
        ("transaction.fee", "fee.adhoc"),
        ("transaction.transfer", "transfer.create"),
    )
    stmts = [
        f"UPDATE {table} SET action_key = '{old}' WHERE action_key = '{new}';"  # noqa: S608
        for new, old in renames
        for table in ("actions.policies", "baton.runs", "baton.subjects")
    ]
    # service_role bypasses the FORCE row-level security on these tables (see the upgrade SQL).
    # RESET ROLE at the end so alembic can stamp alembic_version in this same transaction.
    op.get_bind().exec_driver_sql(
        "SET LOCAL ROLE service_role;\n" + "\n".join(stmts) + "\nRESET ROLE;"
    )
