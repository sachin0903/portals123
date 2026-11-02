-- 0239 rewrote the seeded fact-finds onto the closed field types, and the statement
-- updated nothing. documents.form_fields is FORCE RLS, the migrator is not BYPASSRLS,
-- and that revision ran the UPDATE as itself. A fresh database that applies the
-- corrected 0239 matches zero rows here. A database that already applied the original
-- 0239 still has the handwritten lists, which is what a new journey shows.
--
-- The journey reads the live form. The published flow version does not carry the
-- options, so this is the whole change a journey started afterwards needs. A form a
-- firm authored under another code is left as it is.

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
