-- The organisation fact-find printed its entity type and classification options as their
-- lowercase keys ("charity", "trust") beside the capitalised organisation types. The seed now
-- labels them (`_keyed_opts` in app/flows/onboarding_seed.py); this brings the forms already
-- seeded into line without a reseed.
--
-- Only the LABELS change. The values stay the keys, which is what answers already hold and what
-- `_cond("entityType", "equals", "charity")` reads. A row whose options are no longer the seeded
-- list was edited by the firm and is left as it is.
--
-- documents.form_fields is FORCE RLS and the migrator is not BYPASSRLS (see 0241).

SET LOCAL ROLE service_role;

UPDATE documents.form_fields AS ff
   SET options = '{"options":[
         {"value":"corporate","label":"Corporate"},{"value":"trust","label":"Trust"},
         {"value":"charity","label":"Charity"}]}'::jsonb
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code = 'organisation_core'
   AND ff.field_key = 'entityType'
   AND ff.options = '{"options":[
         {"value":"corporate","label":"corporate"},{"value":"trust","label":"trust"},
         {"value":"charity","label":"charity"}]}'::jsonb;

UPDATE documents.form_fields AS ff
   SET options = '{"options":[
         {"value":"charity","label":"Charity"},{"value":"collective","label":"Collective"},
         {"value":"corporate","label":"Corporate"},{"value":"estate","label":"Estate"},
         {"value":"government","label":"Government"},{"value":"individual","label":"Individual"},
         {"value":"pension","label":"Pension"},{"value":"trust","label":"Trust"}]}'::jsonb
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code = 'organisation_core'
   AND ff.field_key = 'classification'
   AND ff.options = '{"options":[
         {"value":"charity","label":"charity"},{"value":"collective","label":"collective"},
         {"value":"corporate","label":"corporate"},{"value":"estate","label":"estate"},
         {"value":"government","label":"government"},{"value":"individual","label":"individual"},
         {"value":"pension","label":"pension"},{"value":"trust","label":"trust"}]}'::jsonb;

RESET ROLE;
