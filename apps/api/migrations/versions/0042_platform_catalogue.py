"""platform catalogue: products, models + allocation history, fees, providers, terminology

Revision ID: 0042_platform_catalogue
Revises: 0041_platform_conformed
Create Date: 2026-07-31

The DDL lives in the sibling SQL file (migrations/sql/0042_platform_catalogue.sql) -- that
file is the reviewable artifact; this script just applies it. Runs as the elevated
migrator role (see migrations/env.py).

The firm-configured half of the platform model: products become tenant-scoped with
per-platform references, models gain the fields the providers publish plus versioned
allocations and commentary, fee packages gain what Seccl/SEI add to the fixed/percentage/
tiered rule, and two provider registries and a terminology catalogue arrive. Expand phase:
new tables, new nullable columns, NOT NULL only dropped.
"""

from pathlib import Path

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0042_platform_catalogue"
down_revision: str | None = "0041_platform_conformed"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def _run(filename: str) -> None:
    # exec_driver_sql sends the literal SQL straight to psycopg (no SQLAlchemy bind-param
    # parsing, so `::cast` is safe) as a single multi-statement script. psycopg uses
    # pyformat, so it still scans for `%` placeholders -- double every `%` (in RAISE/
    # format() bodies) so psycopg collapses `%%` back to a literal `%` for the server.
    sql = (_SQL_DIR / filename).read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def upgrade() -> None:
    _run("0042_platform_catalogue.sql")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS platform.entity_links")
    op.execute("DROP SCHEMA IF EXISTS l10n CASCADE")
    # The triggers this migration added to tables that already existed. Dropping the new
    # tables takes their triggers with them; these three outlive their tables, and leaving
    # them makes a down/up cycle fail on "trigger set_updated_at already exists".
    for table in ("products", "models", "fee_packages"):
        op.execute(f"DROP TRIGGER IF EXISTS set_updated_at ON platform.{table}")
    for table in (
        "transfer_provider_products",
        "transfer_providers",
        "third_party_provider_products",
        "third_party_providers",
        "fee_package_platforms",
        "model_commentary",
        "model_allocations",
        "model_versions",
        "product_platforms",
    ):
        op.execute(f"DROP TABLE IF EXISTS platform.{table}")
    for column in (
        "charge_basis",
        "payer",
        "vatable",
        "minimum_amount",
        "maximum_amount",
        "applies_to",
        "effective_from",
        "effective_to",
        "currency",
        "data",
        "created_at",
        "updated_at",
    ):
        op.execute(f"ALTER TABLE platform.fee_packages DROP COLUMN IF EXISTS {column}")
    for column in (
        "code",
        "model_type",
        "aim",
        "target_return",
        "target_return_basis",
        "target_return_horizon_years",
        "benchmark",
        "risk_rating",
        "risk_profile",
        "investment_decision",
        "model_charge",
        "vatable",
        "suspend_rebalances",
        "cash_allocation",
        "currency",
        "status",
        "data",
        "created_at",
        "updated_at",
    ):
        op.execute(f"ALTER TABLE platform.models DROP COLUMN IF EXISTS {column}")
    # The policy goes first: it reads tenant_id, so dropping the column under it fails with
    # "cannot drop column tenant_id because other objects depend on it".
    op.execute("DROP POLICY IF EXISTS products_read ON platform.products")
    for column in (
        "tenant_id",
        "code",
        "currency",
        "is_pension",
        "sort_order",
        "data",
        "created_at",
        "updated_at",
    ):
        op.execute(f"ALTER TABLE platform.products DROP COLUMN IF EXISTS {column}")
    # Restore the global read policy this migration replaced.
    op.execute(
        "CREATE POLICY products_read ON platform.products FOR SELECT TO authenticated USING (true)"
    )
