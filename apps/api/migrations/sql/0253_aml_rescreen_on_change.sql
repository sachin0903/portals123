-- ============================================================================
-- 0253_aml_rescreen_on_change.sql — any change to the details an AML screen
-- searched screens the client again.
--
-- The trigger 0223 put on crm.contacts (first_name, surname, name, dob,
-- nationality: the details ComplyCube screens) keeps its columns. Its function
-- now marks the client's latest portal AML check stale whatever it ended as —
-- clear, refer (allowed past its matches or still under review) or rejected —
-- not only when it was clear. A screen still running is left to finish, and a
-- screen ComplyCube could not run (failed) is not a result to go stale.
--
-- Every passed AML kyc row for the contact is expired in the same statement, not
-- only the one the latest portal check points at. pick_valid reuses any passed
-- AML row (app/flows/kyc.py), so a sibling pass of the previous name, date of
-- birth or nationality would let the next journey skip the new screen. The firm
-- reads an expired AML pass as not started until the client is screened again.
--
-- The queued `aml.rescreen.requested` row is unchanged. The outbox dispatcher
-- hands it to app/complycube/rescreen.py, which screens the client again by
-- itself after a clear or allowed screen, and asks operations after a rejected
-- screen or one under review.
--
-- The function stays SECURITY DEFINER and owned by service_role (BYPASSRLS),
-- as 0223 set it: compliance.checks, crm.kyc_checks and flows.outbox are FORCE
-- RLS, so a function that ran as the writer would change nothing.
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
       AND ch.status IN ('clear', 'refer', 'rejected')
       AND ch.stale_at IS NULL
       AND NOT EXISTS (
         SELECT 1 FROM compliance.checks later
          WHERE later.client_id = ch.client_id
            AND later.kind = 'aml'
            AND later.id > ch.id
       )
    RETURNING ch.uid, ch.kyc_check_id
  ),
  expired AS (
    UPDATE crm.kyc_checks k
       SET status = 'expired', updated_at = now()
     WHERE k.contact_id = NEW.id
       AND k.tenant_id = NEW.tenant_id
       AND k.kind = 'aml'
       AND k.status = 'passed'
       AND EXISTS (SELECT 1 FROM marked)
  )
  INSERT INTO flows.outbox (tenant_id, event_type, payload)
  SELECT NEW.tenant_id,
         'aml.rescreen.requested',
         jsonb_build_object('contact_id', NEW.id, 'check_uid', marked.uid)
    FROM marked;
  RETURN NULL;  -- AFTER trigger: the return value is ignored
END;
$fn$;

ALTER FUNCTION compliance.mark_screen_stale() OWNER TO service_role;
REVOKE ALL ON FUNCTION compliance.mark_screen_stale() FROM PUBLIC;
