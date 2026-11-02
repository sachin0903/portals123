"""compliance: ComplyCube core — portal checks, matches, firm webhook outbox, tipping-off grants

Revision ID: 0221_complycube_core
Revises: 0210_employer_facts_backfill
Create Date: 2026-10-01
"""

from pathlib import Path

from alembic import op

revision: str = "0221_complycube_core"
down_revision: str | None = "0220_instruction_category"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def upgrade() -> None:
    sql = (_SQL_DIR / "0221_complycube_core.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        SET LOCAL lock_timeout = '5s';
        ALTER POLICY audit_log_read ON audit.log USING (
            (tenant_id = ANY (auth.tenant_lineage()) AND entity_type NOT LIKE 'aml.%%')
            OR (tenant_id IS NULL AND auth.is_system())
        );
        DROP TABLE IF EXISTS compliance.webhook_deliveries;
        DROP TABLE IF EXISTS compliance.matches;
        GRANT SELECT ON compliance.events TO authenticated;
        ALTER TABLE compliance.events DROP COLUMN IF EXISTS last_error,
                                      DROP COLUMN IF EXISTS attempts;
        REVOKE SELECT ON compliance.checks FROM authenticated;
        GRANT SELECT ON compliance.checks TO authenticated;
        DROP INDEX IF EXISTS compliance.compliance_checks_kyc_check_idx;
        ALTER TABLE compliance.checks DROP CONSTRAINT checks_status_check;
        ALTER TABLE compliance.checks
          ADD CONSTRAINT checks_status_check
          CHECK (status IN ('pending', 'clear', 'refer', 'rejected'));
        ALTER TABLE compliance.checks
          DROP CONSTRAINT IF EXISTS compliance_checks_kyc_check_fkey,
          DROP CONSTRAINT IF EXISTS compliance_checks_tenant_id_key,
          DROP CONSTRAINT IF EXISTS compliance_checks_attempt_check,
          DROP CONSTRAINT IF EXISTS compliance_checks_origin_check,
          DROP COLUMN IF EXISTS monitoring,
          DROP COLUMN IF EXISTS stale_at,
          DROP COLUMN IF EXISTS decision_comment,
          DROP COLUMN IF EXISTS decided_at,
          DROP COLUMN IF EXISTS decided_by,
          DROP COLUMN IF EXISTS attempt,
          DROP COLUMN IF EXISTS kyc_check_id,
          DROP COLUMN IF EXISTS origin;
        ALTER TABLE crm.kyc_checks DROP CONSTRAINT IF EXISTS kyc_checks_tenant_id_key;
        """
    )
