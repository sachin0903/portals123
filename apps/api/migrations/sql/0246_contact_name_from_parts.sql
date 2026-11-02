-- ============================================================================
-- 0246_contact_name_from_parts.sql — a person's `name` is their first, middle
-- and surname, whoever writes the row.
--
-- crm.contacts.name is the one-line name every consumer falls back to (the
-- dashboard rail, documents.requests.contact_name, ComplyCube when a person
-- lacks a part: complycube/clients.py person_payload splits it). It used to be
-- written four ways: journey kickoff and the partner API wrote first, middle and
-- surname; the client record forms wrote first and surname; a flow's form
-- write-back changed the parts and never the name, so the kickoff spelling
-- stayed after the fact-find corrected it.
--
-- 1. A BEFORE trigger keeps one rule on every write path. For an individual,
--    on INSERT and on an UPDATE that changes a part or sets a new name:
--      * with both a first name and a surname, `name` is first, middle and
--        surname, trimmed and single-spaced;
--      * with only one of them, `name` follows the parts left IF it came from
--        the parts before (the row had both, or its name was its parts), so a
--        cleared surname leaves the name and is not screened again. A name that
--        did not come from the parts (a person known by name alone, before the
--        fact-find filled them) is kept rather than lost to a lone first name;
--      * with neither, `name` is kept.
--    An UPDATE that changes no part and no name leaves `name` exactly as it is,
--    so no row is rewritten that nobody touched. An organisation keeps its own
--    name. Existing names are deliberately NOT backfilled: a row is put right
--    the next time its name is edited.
--
-- 2. 0223's mark_screen_stale counted ANY change of `name`. For a person with
--    both parts ComplyCube is sent the parts, never `name` or a middle name, and
--    every real write path names first_name in its SET (a flow write-back sends
--    all the mapped parts at once). So a fact-find that only added a middle name
--    would now re-name the row under (1) and raise a re-screen for nothing.
--    The trigger is re-created with `name` counting only when ComplyCube would
--    read it: an organisation, or a person without both parts. First name,
--    surname, date of birth and nationality count exactly as before.
--
-- `_search` (0211) is a generated column, computed after BEFORE triggers, so it
-- reads the new name. One transaction: there is no moment without 0223's
-- trigger.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION crm.contact_name_from_parts() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog, public AS $fn$
DECLARE
  first_part text := NULLIF(btrim(NEW.first_name), '');
  middle_part text := NULLIF(btrim(NEW.middle_name), '');
  last_part text := NULLIF(btrim(NEW.surname), '');
  from_parts text := NULLIF(concat_ws(' ', first_part, middle_part, last_part), '');
  old_from_parts text;
BEGIN
  IF NEW.type <> 'individual' OR from_parts IS NULL THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE'
     AND NOT (NEW.first_name, NEW.middle_name, NEW.surname, NEW.name)
             IS DISTINCT FROM (OLD.first_name, OLD.middle_name, OLD.surname, OLD.name) THEN
    RETURN NEW;
  END IF;
  IF first_part IS NOT NULL AND last_part IS NOT NULL THEN
    NEW.name := from_parts;
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' THEN
    old_from_parts := NULLIF(concat_ws(' ',
      NULLIF(btrim(OLD.first_name), ''),
      NULLIF(btrim(OLD.middle_name), ''),
      NULLIF(btrim(OLD.surname), '')), '');
    IF (NULLIF(btrim(OLD.first_name), '') IS NOT NULL AND NULLIF(btrim(OLD.surname), '') IS NOT NULL)
       OR btrim(OLD.name) IS NOT DISTINCT FROM old_from_parts THEN
      NEW.name := from_parts;
    END IF;
  END IF;
  RETURN NEW;
END;
$fn$;

CREATE TRIGGER contact_name_from_parts
  BEFORE INSERT OR UPDATE OF first_name, middle_name, surname, name ON crm.contacts
  FOR EACH ROW
  EXECUTE FUNCTION crm.contact_name_from_parts();

DROP TRIGGER mark_screen_stale ON crm.contacts;
CREATE TRIGGER mark_screen_stale
  AFTER UPDATE OF first_name, surname, name, dob, nationality ON crm.contacts
  FOR EACH ROW
  WHEN (
    OLD.first_name IS DISTINCT FROM NEW.first_name
    OR OLD.surname IS DISTINCT FROM NEW.surname
    OR (
      OLD.name IS DISTINCT FROM NEW.name
      AND (
        NEW.type <> 'individual'
        OR NULLIF(btrim(NEW.first_name), '') IS NULL
        OR NULLIF(btrim(NEW.surname), '') IS NULL
      )
    )
    OR OLD.dob IS DISTINCT FROM NEW.dob
    OR OLD.nationality IS DISTINCT FROM NEW.nationality
  )
  EXECUTE FUNCTION compliance.mark_screen_stale();
