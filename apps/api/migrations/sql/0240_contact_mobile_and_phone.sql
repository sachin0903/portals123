-- Two plain numbers: `mobile` and `phone`.
--
-- `mobile_phone` was the number staff call, and `phone_numbers` was a labelled list
-- (home / work / landline / other) capped at one entry on save. Those labels are
-- ours. Seccl has one `mobile` object and a rarely populated `phone`. Morgan Lloyd
-- has `mobilephone` and `telephone1`. SEI enrolment has one phone, which we send as
-- Mobile, and the file drop can carry a second. A list of labels has no custodian
-- to land in.
--
-- `mobile` is the renamed primary. `phone` is the column 0002 created and 0164 left
-- in place: the first other number, when it is not the same line as the mobile.
-- Entries after the first are not kept — the write path already refused a second,
-- and Morgan Lloyd's `telephone1` only ever read the first.
--
-- `_search` is generated from `mobile_phone` and `search.phone_text(phone_numbers)`,
-- so it has to come off before either column moves, and go back on afterwards.
-- The trigram index is rebuilt by the Alembic wrapper (CONCURRENTLY).

SET LOCAL lock_timeout = '5s';

ALTER TABLE crm.contacts DROP COLUMN _search;

ALTER TABLE crm.contacts RENAME COLUMN mobile_phone TO mobile;

-- One spelling, so `+447700900123` and `07700900123` compare as one line. Same
-- helper 0164 used, and dropped at the end for the same reason: one backfill.
CREATE FUNCTION pg_temp.uk_canonical(number text) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
      WHEN number ~ '^\+?44[1-9]\d{8,9}$'
        THEN '0' || regexp_replace(number, '^\+?44', '')
      ELSE number
    END;
  $$;

-- FORCE-RLS would make this UPDATE a silent no-op. service_role is the lift 0164 uses.
SET LOCAL ROLE service_role;

-- A Seccl object still stored as text. The same unwrap 0164 applied to `phone`.
UPDATE crm.contacts
   SET mobile = NULLIF(substring(mobile FROM '"number"\s*:\s*"([^"]*)"'), '')
 WHERE mobile ~ '"number"\s*:\s*"';

UPDATE crm.contacts AS c
   SET phone = CASE
         WHEN jsonb_typeof(c.phone_numbers) = 'array'
          AND jsonb_array_length(c.phone_numbers) > 0
          AND NULLIF(c.phone_numbers -> 0 ->> 'number', '') IS NOT NULL
          AND pg_temp.uk_canonical(
                regexp_replace(c.phone_numbers -> 0 ->> 'number', '[\s().-]', '', 'g')
              ) IS DISTINCT FROM pg_temp.uk_canonical(
                regexp_replace(COALESCE(c.mobile, ''), '[\s().-]', '', 'g')
              )
           THEN c.phone_numbers -> 0 ->> 'number'
         WHEN NULLIF(c.phone, '') IS NOT NULL
          AND NULLIF(c.mobile, '') IS NOT NULL
          AND pg_temp.uk_canonical(regexp_replace(c.phone, '[\s().-]', '', 'g'))
              IS NOT DISTINCT FROM pg_temp.uk_canonical(
                regexp_replace(c.mobile, '[\s().-]', '', 'g')
              )
           THEN NULL
         ELSE c.phone
       END;

RESET ROLE;

DROP FUNCTION pg_temp.uk_canonical(text);

ALTER TABLE crm.contacts
  DROP CONSTRAINT IF EXISTS contacts_phone_numbers_is_array,
  DROP COLUMN phone_numbers;

DROP FUNCTION IF EXISTS search.phone_text(jsonb);

COMMENT ON COLUMN crm.contacts.mobile IS
  'The client''s mobile. Sent to Seccl as mobile, to Morgan Lloyd as mobilephone, and '
  'to SEI enrolment as the one phone (method Mobile). The custodian merge fills a blank; '
  'a number the firm has typed is left alone.';
COMMENT ON COLUMN crm.contacts.phone IS
  'The client''s other number. Sent to Morgan Lloyd as telephone1. Seccl has no second '
  'phone field, and SEI enrolment has already used its one phone on the mobile, so those '
  'calls omit it. The file-drop merge fills a blank from a second number when the drop has one.';

ALTER TABLE crm.contacts ADD COLUMN _search text GENERATED ALWAYS AS (
  search.normalise(
    uid, name, first_name, middle_name, surname, email,
    mobile, phone, national_identifier
  )
) STORED;

COMMENT ON COLUMN crm.contacts._search IS
  'Search text: search.normalise of the identity, name, email, mobile, phone and '
  'national identifier. Generated; trigram-indexed. Read by Spotlight and the list search boxes.';

-- The fact-find's second question becomes one number. The key stays `other_numbers`
-- so an answer already stored against an in-flight case still resolves.
SET LOCAL ROLE service_role;

UPDATE documents.form_fields AS ff
   SET field_type = 'phoneNumber',
       label = 'Phone'
  FROM documents.forms AS f
 WHERE ff.form_id = f.id
   AND f.code IN ('applicant_core', 'jisa_registered_contact')
   AND ff.field_key = 'other_numbers'
   AND ff.field_type = 'phoneNumbers';

UPDATE documents.form_responses
   SET answers = jsonb_set(
         answers,
         '{other_numbers}',
         to_jsonb(answers -> 'other_numbers' -> 0 ->> 'number')
       )
 WHERE jsonb_typeof(answers -> 'other_numbers') = 'array'
   AND NULLIF(answers -> 'other_numbers' -> 0 ->> 'number', '') IS NOT NULL;

UPDATE documents.form_responses
   SET answers = answers - 'other_numbers'
 WHERE jsonb_typeof(answers -> 'other_numbers') = 'array';

-- Published journeys still map onto the old columns until the next reseed.
UPDATE flows.flow_versions
   SET definition = replace(
         replace(definition::text, '"target": "mobile_phone"', '"target": "mobile"'),
         '"target": "phone_numbers"', '"target": "phone"'
       )::jsonb
 WHERE definition::text LIKE '%mobile_phone%'
    OR definition::text LIKE '%phone_numbers%';

RESET ROLE;
