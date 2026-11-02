-- One PRIMARY MOBILE, plus a list of the client's other numbers.
--
-- The table has carried two named text columns since 0053: `phone` (added by 0002 as the only
-- number) and `mobile_phone` (added by 0053 because "Dynamics has both telephone1 and
-- mobilephone; we had one phone"). Two boxes is the wrong shape for this fact, and it went wrong
-- in three separate ways.
--
-- **Two boxes cannot hold three numbers.** A client with a home line, a work line and a mobile has
-- nowhere to put the third, so it went in the notes, or it overwrote the second, or it was never
-- recorded. The estate we are porting from already knew this: SEI publishes a per-entity
-- `contact_points` model with a `PRIMARY_PHONE_FLAG` precisely because one client has several
-- numbers, and we were collapsing it to two on the way in.
--
-- **Nobody maintained the distinction.** `phone` was meant to be the landline and `mobile_phone`
-- the mobile, but nothing enforced it and three writers disagreed: onboarding's one question wrote
-- `mobile_phone`, the custodian book-merge wrote `phone`, and the client-record form let an adviser
-- type either number in either box. So the column said nothing about the KIND of number it held.
--
-- **And the two were read by different surfaces.** The identity header read `mobile_phone`; the
-- clients listing, the client search, the CSV export and the client portal all read `phone`. The
-- same client showed a different number depending on which page you were on, and
-- `matchesClientQuery` could not find a client by the mobile at all.
--
-- So: `mobile_phone` KEEPS its name and becomes the primary mobile — the one number staff call,
-- and the one every surface now reads — and everything else moves into `phone_numbers`, a jsonb
-- ARRAY of `{"label", "number"}`. `phone` is retained for one expand/contract release so the
-- previous application image can keep reading while this migration runs; no new code reads or
-- writes it, and a later migration may drop it after the old image can no longer be live.
--
-- **The array follows `platform.accounts.bank_accounts` (0133), deliberately.** Same jsonb array,
-- same `jsonb_typeof = 'array'` CHECK, same rule that the order is the firm's and is never sorted,
-- same discipline of one shared per-element normaliser (`contacts.normalise_phone_entry`, which
-- calls the SAME `validate_contact_phone` the primary mobile goes through). A second convention
-- for "a list of small objects on a contact" would be a second set of edge cases to learn.
--
-- ---------------------------------------------------------------------------------------------
-- THE BACKFILL, and why it does not simply move `phone` into the list.
--
-- `phone` holds mobiles. It is the column the custodian merge writes, and all three conformed
-- models put the client's best number there (Seccl coalesces `mobile` before `phone`, SEI resolves
-- `is_primary_phone`). Moving every one of them into a list labelled "landline" would empty the
-- primary mobile for most of the book on the day this ships. So the value is classified:
--
--   * UK mobile shape (07…, +447…, 447…) and `mobile_phone` is empty  -> it IS the primary mobile.
--   * UK mobile shape and `mobile_phone` is already set               -> a second mobile: `other`.
--   * UK landline shape (01…, 02…, 03…, +441/2/3…)                    -> `landline`.
--   * anything else — overseas, unrecognised                          -> `other`, never guessed.
--
-- A wrong LABEL is recoverable by an adviser in one click. A lost NUMBER is not, and a number
-- silently promoted to "the mobile we will text this client on" is worse than either. Nothing here
-- discards a value: every non-empty `phone` ends up in exactly one of the two places.
--
-- The provider-object unwrap runs first. Seccl publishes its numbers as
-- `{"number":"07715215412","isMobile":true,"locale":"en-GB"}` and `phone` is a text column that
-- accepted the whole object for as long as it did, so rows still hold one. `app.contacts.phone_expr`
-- defends the READ path; this is the same regex, applied once to the stored value, so the defence
-- is no longer needed for anything that lands in `phone_numbers`. `mobile_phone` keeps its read-side
-- unwrap, because the merge and the vendor payloads still write it.

SET LOCAL lock_timeout = '5s';

ALTER TABLE crm.contacts
  ADD COLUMN IF NOT EXISTS phone_numbers jsonb;

-- An ARRAY or nothing. A bare object would read as one number and then fail every element walk in
-- `normalise_phone_numbers`, the Morgan Lloyd `telephone1` projection and the record's row list.
ALTER TABLE crm.contacts
  ADD CONSTRAINT contacts_phone_numbers_is_array
  CHECK (phone_numbers IS NULL OR jsonb_typeof(phone_numbers) = 'array');

COMMENT ON COLUMN crm.contacts.mobile_phone IS
  'The client''s PRIMARY MOBILE — the one number staff call, and the one the identity header, the '
  'clients listing, the search index, the CSV export and the client portal all read. Written by '
  'onboarding (the fact-find''s one phone question), by the custodian book-merge and by the client '
  'record. Every other number the client has lives in phone_numbers.';
COMMENT ON COLUMN crm.contacts.phone_numbers IS
  'The client''s OTHER numbers, as an ARRAY of {"label","number"} — label is home | work | landline '
  '| other. The order is the firm''s and is never sorted. NULL and [] both mean "none". Validated '
  'per element by contacts.normalise_phone_entry, which runs each number through the same '
  'validate_contact_phone the primary mobile uses. Morgan Lloyd''s telephone1 is the FIRST entry.';

-- One number, one spelling, for the duplicate check below. `+447700900123` and `07700900123` are
-- the same line; as text they are not. Only a FULL international UK number is rewritten to its
-- national form — `^\+?44` followed by a valid trunk digit and the right length — so a national
-- number that merely begins "44" is returned untouched rather than mangled into a different one.
-- Dropped at the end of the migration: it exists to compare two columns during the backfill and
-- has no caller afterwards.
CREATE FUNCTION pg_temp.uk_canonical(number text) RETURNS text
  LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
      WHEN number ~ '^\+?44[1-9]\d{8,9}$'
        THEN '0' || regexp_replace(number, '^\+?44', '')
      ELSE number
    END;
  $$;

-- FORCE-RLS on crm.contacts would make these UPDATEs no-ops that report success (the migrator is
-- not BYPASSRLS); service_role is the same lift 0059 documents and 0153 uses.
SET LOCAL ROLE service_role;

-- One pass, one classification. `compact` is the number with the separators `validate_contact_phone`
-- also strips, so the shape tests below see what the application would see.
WITH unwrapped AS (
  SELECT
    id,
    NULLIF(
      regexp_replace(
        CASE
          WHEN phone ~ '"number"\s*:\s*"'
            THEN COALESCE(substring(phone from '"number"\s*:\s*"([^"]*)"'), '')
          ELSE COALESCE(phone, '')
        END,
        '[\s().-]', '', 'g'
      ),
      ''
    ) AS compact,
    NULLIF(
      CASE
        WHEN mobile_phone ~ '"number"\s*:\s*"'
          THEN COALESCE(substring(mobile_phone from '"number"\s*:\s*"([^"]*)"'), '')
        ELSE COALESCE(mobile_phone, '')
      END,
      ''
    ) AS existing_mobile
  FROM crm.contacts
), classified AS (
  SELECT
    id,
    compact,
    existing_mobile,
    -- A UK mobile: 07 followed by nine digits, in national or either international spelling.
    (compact ~ '^(\+?44|0)7\d{9}$')                      AS is_uk_mobile,
    -- A UK landline: the 01 / 02 / 03 ranges, same three spellings.
    (compact ~ '^(\+?44|0)[123]\d{8,9}$')                AS is_uk_landline
  FROM unwrapped
  WHERE compact IS NOT NULL
    -- THE SAME NUMBER IN BOTH COLUMNS IS ONE NUMBER, not two. Plenty of rows carry it twice: the
    -- record's form let an adviser type either number in either box, and the custodian merge wrote
    -- `phone` for a client whose mobile onboarding had already recorded. Without this the row
    -- keeps its primary mobile AND gains "Other: <the same number>" — a duplicate on the client
    -- record, in the CSV and in the Morgan Lloyd payload, which reads as a data-quality problem
    -- the firm then has to go and clean up by hand.
    --
    -- Compared on a UK-canonical form, because compacting alone is not enough: `+447700900123`
    -- and `07700900123` are the same number and compare unequal as text, which would miss exactly
    -- the rows most likely to hold both spellings (one column written by a vendor in E.164, the
    -- other typed nationally by an adviser). The rewrite is applied ONLY to a full international
    -- UK number — a national number that merely happens to start "44" is left alone, because
    -- mangling it here would make two DIFFERENT numbers compare equal and silently drop one.
    AND pg_temp.uk_canonical(compact) IS DISTINCT FROM pg_temp.uk_canonical(
          regexp_replace(COALESCE(existing_mobile, ''), '[\s().-]', '', 'g')
        )
)
UPDATE crm.contacts AS c
   SET mobile_phone = CASE
         WHEN k.is_uk_mobile AND k.existing_mobile IS NULL THEN k.compact
         ELSE c.mobile_phone
       END,
       phone_numbers = CASE
         WHEN k.is_uk_mobile AND k.existing_mobile IS NULL THEN c.phone_numbers
         ELSE COALESCE(c.phone_numbers, '[]'::jsonb) || jsonb_build_array(
                jsonb_build_object(
                  'label', CASE WHEN k.is_uk_landline THEN 'landline' ELSE 'other' END,
                  'number', k.compact
                )
              )
       END
  FROM classified AS k
 WHERE k.id = c.id;

RESET ROLE;

-- `pg_temp` is per-SESSION, and alembic runs every migration on one connection — so without this
-- the helper would still be resolvable from whatever migration runs next. It answered one question
-- during one backfill.
DROP FUNCTION pg_temp.uk_canonical(text);
