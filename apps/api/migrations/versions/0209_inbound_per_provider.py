"""One landing schema per custodian

Revision ID: 0209_inbound_per_provider
Revises: 0208_contact_currency
Create Date: 2026-09-30

``alembic_version.version_num`` is ``varchar(32)``; this id is 26 characters.

Seccl and SEI were delivering into the same ``inbound.{uid}__clients`` table. SEI's
merge then inserted Seccl's clients under ``provider = sei``. The table name has no
room for a provider token, so the schema is the provider's.
"""

from pathlib import Path

from alembic import op

revision: str = "0209_inbound_per_provider"
down_revision: str | None = "0208_contact_currency"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0209_inbound_per_provider.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS inbound_seccl CASCADE")
    op.execute("DROP SCHEMA IF EXISTS inbound_sei CASCADE")
    op.execute("DROP SCHEMA IF EXISTS inbound_morgan_lloyd CASCADE")
