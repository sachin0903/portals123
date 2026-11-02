"""Connect enabled SEI firms to the wrappers SEI opens.

Revision ID: 0259_sei_product_custodian
Revises: 0258_merge_alerted_and_fatca
Create Date: 2026-10-16

``alembic_version.version_num`` is ``varchar(32)``; this id is 26 characters.
Main already joined the FATCA head in ``0258_merge_alerted_and_fatca``, so this
revision follows that single head.

The SQL file says why. Downgrade does not remove the mappings: a row with no
external id is also the shape an operator saves, and deleting it would
disconnect the shelf again.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0259_sei_product_custodian"
down_revision: str | None = "0258_merge_alerted_and_fatca"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic reads these off the module. The reference keeps them in use.
_ = (revision, down_revision, branch_labels, depends_on)

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0259_sei_product_custodian.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    """The unmapped shelf is the bug. Leave the rows in place."""
