"""l10n.terms: the `instrument` default label becomes Instrument, matching the canonical noun

Revision ID: 0058_instrument_term_default
Revises: 0057_fee_packages_external_key
Create Date: 2026-08-04

The DDL and its reasoning live in the sibling SQL file -- that file is the reviewable
artifact; this script just applies it. Runs as the elevated migrator role (migrations/env.py).
"""

from pathlib import Path

from alembic import op

revision: str = "0058_instrument_term_default"
down_revision: str | None = "0057_fee_packages_external_key"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0058_instrument_term_default.sql")


def downgrade() -> None:
    # Back to the 0042 seed, and only for a row still carrying what this migration set — a label
    # an operator has changed since is theirs, not ours to revert.
    op.execute(
        """
        SET LOCAL ROLE service_role;
        UPDATE l10n.terms
           SET default_label = 'Asset', default_plural = 'Assets',
               description = 'The security held. "Asset" and "instrument" are the usual disagreement.'
         WHERE key = 'instrument' AND default_label = 'Instrument';
        RESET ROLE;
        """
    )
