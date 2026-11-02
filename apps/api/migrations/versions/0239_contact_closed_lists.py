"""Narrow gender and marital status to the values every custodian stores.

Revision ID: 0239_contact_closed_lists
Revises: 0238_named_portal
Create Date: 2026-10-08

``alembic_version.version_num`` is ``varchar(32)``; this id is 25 characters.

The DDL and its reasoning live in the sibling SQL file. Downgrade restores the wider
CHECKs. Values cleared by the upgrade are not put back.
"""

from pathlib import Path

from alembic import op

revision: str = "0239_contact_closed_lists"
down_revision: str | None = "0238_named_portal"
_ = (revision, down_revision)  # Alembic reads these names off the module

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

_DOWN = """
ALTER TABLE crm.contacts DROP CONSTRAINT contacts_marital_status_check;
ALTER TABLE crm.contacts
  ADD CONSTRAINT contacts_marital_status_check
  CHECK (marital_status IS NULL OR marital_status IN
    ('single', 'married', 'civil_partner', 'divorced', 'cohabiting', 'widowed'));

ALTER TABLE crm.contacts DROP CONSTRAINT contacts_gender_check;
ALTER TABLE crm.contacts
  ADD CONSTRAINT contacts_gender_check
  CHECK (gender IS NULL OR gender IN ('male', 'female', 'other', 'not_specified'));

COMMENT ON COLUMN crm.contacts.marital_status IS
  'Canonical marital status, mapped per counterparty at the boundary.';

COMMENT ON COLUMN crm.contacts.gender IS
  'Client gender. Required by Morgan Lloyd (cam_createcontact gendercode); their option set '
  'admits only male/female, so other/not_specified are stored but cannot be pushed there.';

SET LOCAL ROLE service_role;

UPDATE documents.form_fields AS ff
   SET field_type = 'select',
       options = '{"options":[
         {"value":"Mr","label":"Mr"},{"value":"Mrs","label":"Mrs"},
         {"value":"Miss","label":"Miss"},{"value":"Ms","label":"Ms"},
         {"value":"Dr","label":"Dr"},{"value":"Prof","label":"Prof"},
         {"value":"Master","label":"Master"},{"value":"Capt","label":"Capt"},
         {"value":"Reverend","label":"Reverend"},{"value":"Sir","label":"Sir"},
         {"value":"Lord","label":"Lord"},{"value":"Lady","label":"Lady"}]}'::jsonb
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code IN ('applicant_core', 'jisa_child', 'jisa_registered_contact')
   AND ff.field_key = 'title'
   AND ff.field_type = 'title';

UPDATE documents.form_fields AS ff
   SET field_type = 'select',
       options = '{"options":[
         {"value":"male","label":"Male"},{"value":"female","label":"Female"}]}'::jsonb
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code IN ('applicant_core', 'jisa_child')
   AND ff.field_key = 'gender'
   AND ff.field_type = 'gender';

UPDATE documents.form_fields AS ff
   SET field_type = 'combobox',
       options = '{"options":[
         {"value":"Single","label":"Single"},{"value":"Married","label":"Married"},
         {"value":"Divorced","label":"Divorced"},{"value":"Widowed","label":"Widowed"}]}'::jsonb
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code = 'applicant_pension'
   AND ff.field_key = 'pension.maritalStatus'
   AND ff.field_type = 'marital';

UPDATE documents.form_fields AS ff
   SET field_type = 'combobox',
       options = '{"options":[
         {"value":"Employed","label":"Employed"},{"value":"Retired","label":"Retired"},
         {"value":"Self-Employed","label":"Self-Employed"},
         {"value":"Unemployed","label":"Unemployed"},
         {"value":"Care Under 16s","label":"Care Under 16s"},
         {"value":"Care Over 16","label":"Care Over 16"},
         {"value":"Education","label":"Education"},{"value":"Other","label":"Other"}]}'::jsonb
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code = 'applicant_pension'
   AND ff.field_key = 'pension.employmentStatus'
   AND ff.field_type = 'employment';

RESET ROLE;
"""


def upgrade() -> None:
    sql = (
        (_SQL_DIR / "0239_contact_closed_lists.sql").read_text(encoding="utf-8").replace("%", "%%")
    )
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(_DOWN)
