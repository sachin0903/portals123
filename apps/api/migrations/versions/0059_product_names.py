"""products: a readable `name`, and the wrapper code in `code`

Revision ID: 0059_product_names
Revises: 0058_instrument_term_default
Create Date: 2026-08-04

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0059_product_names"
down_revision: str | None = "0058_instrument_term_default"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0059_product_names.sql")


def downgrade() -> None:
    # Put the code back in `name` only where `name` is still the label this migration set, so a
    # firm's own renaming survives the round trip. `code` is left populated: it is the wrapper's
    # identity and nothing is improved by removing it.
    op.execute(
        """
        SET LOCAL ROLE service_role;
        UPDATE platform.products
           SET name = product_type::text
         WHERE name IN ('General Investment Account', 'Individual Savings Account',
                        'Junior Individual Savings Account', 'Self-Invested Personal Pension',
                        'Third-party Onshore Bond', 'Third-party Offshore Bond',
                        'Third-party Pension');
        RESET ROLE;
        """
    )
