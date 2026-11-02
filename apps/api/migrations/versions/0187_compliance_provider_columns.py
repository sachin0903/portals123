"""compliance: query columns for each provider resource

Revision ID: 0187_provider_columns
Revises: 0186_compliance
Create Date: 2026-09-24

`0186_compliance` may already be applied, so these columns are a new revision.
`body` stays the untouched payload. The revision id is short because
`alembic_version.version_num` is varchar(32).
"""

from pathlib import Path

from alembic import op

revision: str = "0187_provider_columns"
down_revision: str | None = "0186_compliance"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0187_compliance_provider_columns.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql.replace("%", "%%"))


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        DROP INDEX IF EXISTS compliance.compliance_clients_external_id_idx;
        ALTER TABLE compliance.clients
          DROP COLUMN IF EXISTS entity_name,
          DROP COLUMN IF EXISTS email,
          DROP COLUMN IF EXISTS mobile,
          DROP COLUMN IF EXISTS telephone,
          DROP COLUMN IF EXISTS external_id,
          DROP COLUMN IF EXISTS joined_date,
          DROP COLUMN IF EXISTS client_type,
          DROP COLUMN IF EXISTS first_name,
          DROP COLUMN IF EXISTS middle_name,
          DROP COLUMN IF EXISTS last_name,
          DROP COLUMN IF EXISTS dob,
          DROP COLUMN IF EXISTS gender,
          DROP COLUMN IF EXISTS nationality,
          DROP COLUMN IF EXISTS birth_country,
          DROP COLUMN IF EXISTS ssn,
          DROP COLUMN IF EXISTS social_insurance_number,
          DROP COLUMN IF EXISTS national_identity_number,
          DROP COLUMN IF EXISTS tax_identification_number,
          DROP COLUMN IF EXISTS company_name,
          DROP COLUMN IF EXISTS website,
          DROP COLUMN IF EXISTS registration_number,
          DROP COLUMN IF EXISTS incorporation_country,
          DROP COLUMN IF EXISTS incorporation_type;
        ALTER TABLE compliance.addresses
          DROP COLUMN IF EXISTS address_type,
          DROP COLUMN IF EXISTS property_number,
          DROP COLUMN IF EXISTS building_name,
          DROP COLUMN IF EXISTS to_date;
        ALTER TABLE compliance.documents
          DROP COLUMN IF EXISTS document_type,
          DROP COLUMN IF EXISTS document_number,
          DROP COLUMN IF EXISTS issuing_state;
        ALTER TABLE compliance.live_photos
          DROP COLUMN IF EXISTS perform_liveness_check,
          DROP COLUMN IF EXISTS content_type,
          DROP COLUMN IF EXISTS size_bytes,
          DROP COLUMN IF EXISTS download_link;
        ALTER TABLE compliance.checks
          DROP COLUMN IF EXISTS provider_type,
          DROP COLUMN IF EXISTS provider_status,
          DROP COLUMN IF EXISTS outcome,
          DROP COLUMN IF EXISTS entity_name,
          DROP COLUMN IF EXISTS enable_monitoring,
          DROP COLUMN IF EXISTS client_consent,
          DROP COLUMN IF EXISTS provider_document_id,
          DROP COLUMN IF EXISTS provider_address_id,
          DROP COLUMN IF EXISTS provider_live_photo_id,
          DROP COLUMN IF EXISTS provider_live_video_id;
        """
    )
