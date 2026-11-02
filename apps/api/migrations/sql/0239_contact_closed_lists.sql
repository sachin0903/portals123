-- The book keeps a personal fact only when every custodian can store it.
--
-- Marital status was a superset of Morgan Lloyd's family status. Civil partnership and
-- cohabiting have no Seccl value and no SEI value that means the same thing, so a row
-- holding either of them could not be sent whole. Gender's `other` and `not_specified`
-- are on no custodian. Those four stored values are cleared, then the CHECKs shrink to
-- the intersection. Title stays free text: a synced Baroness is still a title, and a
-- CHECK would refuse the row.
--
-- Employment is unchanged. SEI publishes no employment field, and the eight values are
-- the ones Seccl and Morgan Lloyd both accept.

-- FORCE RLS makes a DML statement from migrator a silent no-op. service_role bypasses it.
SET LOCAL ROLE service_role;

UPDATE crm.contacts
   SET marital_status = NULL
 WHERE marital_status IN ('civil_partner', 'cohabiting');

UPDATE crm.contacts
   SET gender = NULL
 WHERE gender IN ('other', 'not_specified');

RESET ROLE;

ALTER TABLE crm.contacts DROP CONSTRAINT contacts_marital_status_check;
ALTER TABLE crm.contacts
  ADD CONSTRAINT contacts_marital_status_check
  CHECK (marital_status IS NULL OR marital_status IN
    ('single', 'married', 'divorced', 'widowed'));

ALTER TABLE crm.contacts DROP CONSTRAINT contacts_gender_check;
ALTER TABLE crm.contacts
  ADD CONSTRAINT contacts_gender_check
  CHECK (gender IS NULL OR gender IN ('male', 'female'));

COMMENT ON COLUMN crm.contacts.marital_status IS
  'single, married, divorced or widowed — the four every custodian can store. '
  'See docs/client-vocabularies.md.';

COMMENT ON COLUMN crm.contacts.gender IS
  'male or female. See docs/client-vocabularies.md.';

-- The seeded fact-finds carry their own copy of these lists. Point them at the field
-- types, which own the options. A form a firm authored under another code is left as it is.
-- Same role switch as the contact updates: documents.form_fields is FORCE RLS.
SET LOCAL ROLE service_role;

UPDATE documents.form_fields AS ff
   SET field_type = 'title', options = NULL
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code IN ('applicant_core', 'jisa_child', 'jisa_registered_contact')
   AND ff.field_key = 'title'
   AND ff.field_type = 'select';

UPDATE documents.form_fields AS ff
   SET field_type = 'gender', options = NULL
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code IN ('applicant_core', 'jisa_child')
   AND ff.field_key = 'gender'
   AND ff.field_type = 'select';

UPDATE documents.form_fields AS ff
   SET field_type = 'marital', options = NULL
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code = 'applicant_pension'
   AND ff.field_key = 'pension.maritalStatus'
   AND ff.field_type = 'combobox';

UPDATE documents.form_fields AS ff
   SET field_type = 'employment', options = NULL
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code = 'applicant_pension'
   AND ff.field_key = 'pension.employmentStatus'
   AND ff.field_type = 'combobox';

RESET ROLE;
