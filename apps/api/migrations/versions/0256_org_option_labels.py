"""Capitalise the seeded organisation fact-find's entity type and classification options.

Revision ID: 0256_org_option_labels
Revises: 0255_drop_review_permissions
Create Date: 2026-10-15

``alembic_version.version_num`` is ``varchar(32)``; this id is 22 characters.

The SQL file says why. Labels only; the stored values are unchanged. Downgrade puts the
lowercase labels back on the rows this upgrade wrote.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0256_org_option_labels"
down_revision: str | None = "0255_drop_review_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_DOWN = """
SET LOCAL ROLE service_role;

UPDATE documents.form_fields AS ff
   SET options = '{"options":[
         {"value":"corporate","label":"corporate"},{"value":"trust","label":"trust"},
         {"value":"charity","label":"charity"}]}'::jsonb
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code = 'organisation_core'
   AND ff.field_key = 'entityType'
   AND ff.options = '{"options":[
         {"value":"corporate","label":"Corporate"},{"value":"trust","label":"Trust"},
         {"value":"charity","label":"Charity"}]}'::jsonb;

UPDATE documents.form_fields AS ff
   SET options = '{"options":[
         {"value":"charity","label":"charity"},{"value":"collective","label":"collective"},
         {"value":"corporate","label":"corporate"},{"value":"estate","label":"estate"},
         {"value":"government","label":"government"},{"value":"individual","label":"individual"},
         {"value":"pension","label":"pension"},{"value":"trust","label":"trust"}]}'::jsonb
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code = 'organisation_core'
   AND ff.field_key = 'classification'
   AND ff.options = '{"options":[
         {"value":"charity","label":"Charity"},{"value":"collective","label":"Collective"},
         {"value":"corporate","label":"Corporate"},{"value":"estate","label":"Estate"},
         {"value":"government","label":"Government"},{"value":"individual","label":"Individual"},
         {"value":"pension","label":"Pension"},{"value":"trust","label":"Trust"}]}'::jsonb;

RESET ROLE;
"""


def upgrade() -> None:
    sql = (_SQL_DIR / "0256_org_option_labels.sql").read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN)
