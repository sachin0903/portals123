"""crm: a client's W-9 or W-8 answer points at the signed form, and a change of answer unlinks it

Revision ID: 0261_contact_fatca_document
Revises: 0260_sei_money_holds
Create Date: 2026-10-16

``alembic_version.version_num`` is ``varchar(32)``; this id is 27 characters.

Downgrade drops the link, its trigger and index, and puts 0040's new-tenant seed back. The
``fatca_form`` type is removed from every firm that has no document of that type; a firm that
filed one keeps the type, so the document still reads with its label. The documents are kept.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0261_contact_fatca_document"
down_revision: str | None = "0260_sei_money_holds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_ = (revision, down_revision, branch_labels, depends_on)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

DOWN = """
SET LOCAL lock_timeout = '5s';

DROP TRIGGER IF EXISTS contacts_fatca_document_follows_status ON crm.contacts;
DROP FUNCTION IF EXISTS crm.unlink_fatca_document();
DROP INDEX IF EXISTS crm.contacts_fatca_document_idx;
ALTER TABLE crm.contacts DROP COLUMN IF EXISTS fatca_document_id;

CREATE OR REPLACE FUNCTION documents.seed_default_document_types()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO documents.document_types (tenant_id, kind, slug, label, sort_order)
    VALUES
        (NEW.id, 'document', 'report', 'Report', 1),
        (NEW.id, 'document', 'statement', 'Statement', 2),
        (NEW.id, 'document', 'agreement', 'Agreement', 3),
        (NEW.id, 'document', 'identity', 'Identity', 4),
        (NEW.id, 'document', 'tax', 'Tax', 5),
        (NEW.id, 'document', 'illustration', 'Illustration', 7),
        (NEW.id, 'document', 'application', 'Application', 8),
        (NEW.id, 'document', 'letter', 'Letter', 9),
        (NEW.id, 'file', 'passport', 'Passport', 1),
        (NEW.id, 'file', 'national_id', 'National ID', 2),
        (NEW.id, 'file', 'proof_of_address', 'Proof of address', 3),
        (NEW.id, 'file', 'other', 'Other file', 9)
    ON CONFLICT (tenant_id, kind, slug) DO NOTHING;
    RETURN NEW;
END;
$$;

SET LOCAL ROLE service_role;
DELETE FROM documents.document_types AS dt
 WHERE dt.kind = 'document'
   AND dt.slug = 'fatca_form'
   AND NOT EXISTS (
     SELECT 1 FROM documents.documents AS d
      WHERE d.tenant_id = dt.tenant_id
        AND d.document_type = 'fatca_form'
   );
RESET ROLE;
"""


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0261_contact_fatca_document.sql")
        .read_text(encoding="utf-8")
        .replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWN)
