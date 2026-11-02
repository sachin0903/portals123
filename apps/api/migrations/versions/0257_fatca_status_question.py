"""Additional information asks Is a US person? and, separately, for the W-9 or W-8 held.

Revision ID: 0257_fatca_status_question
Revises: 0256_org_option_labels
Create Date: 2026-10-15

``alembic_version.version_num`` is ``varchar(32)``; this id is 26 characters.

Downgrade removes the new question and its mapping and puts the old hint back. Answers
already given, and the W-9 / W-8 on a client, are kept.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0257_fatca_status_question"
down_revision: str | None = "0256_org_option_labels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_DOWN = """
SET LOCAL ROLE service_role;

UPDATE flows.flow_versions
   SET definition = replace(
         definition::text,
         ', {"source": "fatca_status", "target": "fatca_status"}',
         ''
       )::jsonb
 WHERE definition::text LIKE '%%{"source": "fatca_status", "target": "fatca_status"}%%';

WITH gone AS (
  DELETE FROM documents.form_fields AS ff
   USING documents.forms AS f
   WHERE ff.form_id = f.id
     AND f.code = 'applicant_offshore'
     AND ff.field_key = 'fatca_status'
     AND ff.label = 'Does this client have a W-9 or a W-8?'
  RETURNING ff.form_id, ff.sort_order
)
UPDATE documents.form_fields AS ff
   SET sort_order = ff.sort_order - 1
  FROM gone
 WHERE ff.form_id = gone.form_id
   AND ff.sort_order > gone.sort_order;

UPDATE documents.form_fields AS ff
   SET description = 'Yes records a W-9. No records a W-8.'
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code = 'applicant_offshore'
   AND ff.field_key = 'us_person'
   AND ff.description = 'A US citizen or US tax resident.';

RESET ROLE;
"""


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0257_fatca_status_question.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN)
