"""Add per-product currency allow-lists

Revision ID: 0174_product_currencies
Revises: 0173_sipp_illustration
Create Date: 2026-09-16
"""

from pathlib import Path

from alembic import op

revision: str = "0174_product_currencies"
down_revision: str | None = "0173_sipp_illustration"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0174_product_currencies.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP TRIGGER IF EXISTS products_validate_currencies ON platform.products;
        DROP FUNCTION IF EXISTS auth.validate_product_currencies();
        CREATE OR REPLACE FUNCTION auth.validate_tenant_currencies() RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, auth, ref
        AS $$
        BEGIN
          IF EXISTS (
            SELECT 1
              FROM unnest(NEW.allowed_currencies) AS configured(code)
              LEFT JOIN ref.currencies known ON known.code = configured.code
             WHERE known.code IS NULL
          ) THEN
            RAISE EXCEPTION 'allowed_currencies contains an unknown currency code'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$;
        ALTER TABLE platform.products DROP COLUMN IF EXISTS allowed_currencies;
        """
    )
