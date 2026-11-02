-- Additional information asks two questions where it asked one. Is a US person? stays
-- required and records only Yes or No. Does this client have a W-9 or a W-8? is new and
-- optional, because only a client investing in US assets has one.
--
-- documents.form_fields and flows.flow_versions are FORCE RLS and the migrator is not
-- BYPASSRLS, so this runs as service_role (0241 says why).

SET LOCAL ROLE service_role;

-- The old hint said Yes records a W-9. A hint a firm rewrote is left as it is.
UPDATE documents.form_fields AS ff
   SET description = 'A US citizen or US tax resident.'
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code = 'applicant_offshore'
   AND ff.field_key = 'us_person'
   AND ff.description = 'Yes records a W-9. No records a W-8.';

-- The new question goes straight after Is a US person?, and every later field moves down
-- one place. A form with no us_person field, or one that already has a fatca_status field,
-- is left as it is. One statement, so the shift and the insert read the same forms.
WITH us AS (
  SELECT ff.tenant_id, ff.form_id, ff.sort_order
    FROM documents.form_fields AS ff
    JOIN documents.forms AS f ON f.id = ff.form_id
   WHERE f.code = 'applicant_offshore'
     AND ff.field_key = 'us_person'
     AND NOT EXISTS (
       SELECT 1
         FROM documents.form_fields AS held
        WHERE held.form_id = ff.form_id
          AND held.field_key = 'fatca_status'
     )
), shifted AS (
  UPDATE documents.form_fields AS ff
     SET sort_order = ff.sort_order + 1
    FROM us
   WHERE ff.form_id = us.form_id
     AND ff.sort_order > us.sort_order
  RETURNING ff.id
)
INSERT INTO documents.form_fields
  (uid, tenant_id, form_id, field_key, field_type, label, section, description, required,
   validation, options, sort_order)
SELECT public.nanoid(12), us.tenant_id, us.form_id, 'fatca_status', 'select',
       'Does this client have a W-9 or a W-8?', 'Additional information',
       'Only needed for US assets.', false, '{"width": 6}'::jsonb,
       '{"options": [{"value": "w9", "label": "W-9"}, {"value": "w8", "label": "W-8"}]}'::jsonb,
       us.sort_order + 1
  FROM us;

-- A journey writes answers to the client through its published version's mapping, so the
-- new answer needs a mapping there too. A journey already running gets it as well: a blank
-- answer maps to nothing.
UPDATE flows.flow_versions
   SET definition = replace(
         definition::text,
         '{"source": "us_person", "target": "us_person"}',
         '{"source": "us_person", "target": "us_person"}, '
         '{"source": "fatca_status", "target": "fatca_status"}'
       )::jsonb
 WHERE definition::text LIKE '%{"source": "us_person", "target": "us_person"}%'
   AND definition::text NOT LIKE '%"source": "fatca_status"%';

RESET ROLE;
