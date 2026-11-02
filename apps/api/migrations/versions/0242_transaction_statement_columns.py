"""platform.transactions -- the nine statement facts the conform step derives and the book dropped

Revision ID: 0242_stmt_columns
Revises: 0241_seeded_form_lists
Create Date: 2026-10-08

``alembic_version.version_num`` is ``varchar(32)``; this id is 16 characters.

`report_group_kind` (the custodian's own statement bucket, from Seccl's `/static/transactiontype`
reference data), the dealing/other charge split, `fee_type`, `book_cost`, `realised_gain`,
`trade_date` (a dividend's ex-date), `equalisation_payment` and `subscription_amount`. All nine
are already computed in the provider layer and were selected away by `book_sink`. DDL in the
sibling SQL.
"""

from pathlib import Path

from alembic import op

revision: str = "0242_stmt_columns"
down_revision: str | None = "0241_seeded_form_lists"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_COLUMNS = (
    "report_group_kind",
    "dealing_charges",
    "other_charges",
    "fee_type",
    "book_cost",
    "realised_gain",
    "trade_date",
    "equalisation_payment",
    "subscription_amount",
)


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0242_transaction_statement_columns.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # The index goes with its columns, so dropping the columns is enough.
    bind = op.get_bind()
    for column in _COLUMNS:
        bind.exec_driver_sql(
            f"ALTER TABLE platform.transactions DROP COLUMN IF EXISTS {column}"  # noqa: S608
        )
