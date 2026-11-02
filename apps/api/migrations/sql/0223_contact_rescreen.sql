-- ============================================================================
-- 0223_contact_rescreen.sql — a clear AML screen goes stale when the details it
-- screened change.
--
-- An UPDATE that changes a client's name (first_name, surname or name), date of
-- birth or nationality marks the client's latest AML check stale
-- (compliance.checks.stale_at) when that check is a clear portal check, and
-- queues one `aml.rescreen.requested` row on flows.outbox. The outbox
-- dispatcher hands the row to app/complycube/rescreen.py, which raises the
-- operations task to screen the client again.
--
-- It is a trigger on crm.contacts so that every write path reaches it: the
-- client record's PATCH (app/contacts.py), a flow's form write-back, the
-- partner API's client PATCH, and the provider pipelines and imports that write
-- crm.contacts directly.
--
-- "Latest" is the rule the AML console's stale view uses: no later AML check
-- for the same compliance client. A check that is already stale is not marked
-- again, so a second change before the client is screened again queues nothing.
--
-- The function is SECURITY DEFINER and owned by service_role (BYPASSRLS), the
-- 0001 device: compliance.checks and flows.outbox are FORCE RLS, so a function
-- that ran as the writer or as the migrator would update and insert nothing.
-- Every name in the body is schema-qualified. `public` is on the search path
-- for the flows.outbox uid default: public.nanoid calls pgcrypto's
-- gen_random_bytes unqualified.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION compliance.mark_screen_stale() RETURNS trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $fn$
BEGIN
  WITH marked AS (
    UPDATE compliance.checks ch
       SET stale_at = now()
      FROM compliance.clients cl
     WHERE cl.id = ch.client_id
       AND cl.contact_id = NEW.id
       AND cl.tenant_id = NEW.tenant_id
       AND ch.kind = 'aml'
       AND ch.origin = 'portal'
       AND ch.status = 'clear'
       AND ch.stale_at IS NULL
       AND NOT EXISTS (
         SELECT 1 FROM compliance.checks later
          WHERE later.client_id = ch.client_id
            AND later.kind = 'aml'
            AND later.id > ch.id
       )
    RETURNING ch.uid
  )
  INSERT INTO flows.outbox (tenant_id, event_type, payload)
  SELECT NEW.tenant_id,
         'aml.rescreen.requested',
         jsonb_build_object('contact_id', NEW.id, 'check_uid', marked.uid)
    FROM marked;
  RETURN NULL;  -- AFTER trigger: the return value is ignored
END;
$fn$;

CREATE TRIGGER mark_screen_stale
  AFTER UPDATE OF first_name, surname, name, dob, nationality ON crm.contacts
  FOR EACH ROW
  WHEN (
    OLD.first_name IS DISTINCT FROM NEW.first_name
    OR OLD.surname IS DISTINCT FROM NEW.surname
    OR OLD.name IS DISTINCT FROM NEW.name
    OR OLD.dob IS DISTINCT FROM NEW.dob
    OR OLD.nationality IS DISTINCT FROM NEW.nationality
  )
  EXECUTE FUNCTION compliance.mark_screen_stale();

-- The new owner needs CREATE on the schema, or ALTER OWNER is refused. USAGE,
-- which 0186 granted, is not enough.
GRANT CREATE ON SCHEMA compliance TO service_role;
ALTER FUNCTION compliance.mark_screen_stale() OWNER TO service_role;
-- A trigger function cannot be called directly, and firing a trigger does not
-- check EXECUTE, so no role needs it.
REVOKE ALL ON FUNCTION compliance.mark_screen_stale() FROM PUBLIC;
