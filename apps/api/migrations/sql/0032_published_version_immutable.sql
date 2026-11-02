-- ============================================================================
-- 0032_published_version_immutable.sql — enforce the published-template-version invariant.
--
-- 0012_templates.sql:43 states it as a bare comment ("Immutable once status = 'published'") with
-- no trigger and no CHECK behind it, and the gap is reachable: PATCH /templates/{uid} applies a
-- renderer change to `max(version_number)` regardless of that version's status
-- (app/routers/templates.py:296-304), so the renderer of a LIVE, already-published version can be
-- swapped underneath every document generated from it. Regeneration determinism — the whole point
-- of pinning documents.documents.template_version_id — depends on this invariant holding.
--
-- The trigger rejects a change to the content (`schema`) or the `renderer` of a row that is
-- ALREADY published. It deliberately does not look at NEW.status, so the state machine still
-- works: draft → published (which writes status/published_at/published_by, and may set the
-- derived input_schema/fixtures in the same statement) passes because OLD.status is 'draft', and
-- published → archived passes because neither guarded column changes.
--
-- Also deliberately unguarded: input_schema / fixtures / metadata / input_form_snapshot. The
-- publish path derives those AFTER the status flip — _capture_form_snapshot (templates.py:749)
-- UPDATEs input_form_snapshot on a row that is by then 'published' — so guarding them would break
-- publishing itself. They are publish-time derivations of the guarded content, not the content.
--
-- Fails with a distinct SQLSTATE so the API layer can map it to a 409 rather than a 500; until it
-- does, an attempt surfaces as an error instead of silently mutating a live version.
-- ============================================================================

SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION documents.reject_published_version_edit() RETURNS trigger
  LANGUAGE plpgsql AS $fn$
BEGIN
  IF OLD.status = 'published'
     AND (NEW.schema   IS DISTINCT FROM OLD.schema
       OR NEW.renderer IS DISTINCT FROM OLD.renderer) THEN
    RAISE EXCEPTION
      'template version % is published and its content/renderer are immutable', OLD.uid
      USING ERRCODE = 'restrict_violation',
            HINT = 'Save a new draft version instead of editing the published one.';
  END IF;
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS reject_published_edit ON documents.template_versions;
CREATE TRIGGER reject_published_edit BEFORE UPDATE ON documents.template_versions
  FOR EACH ROW EXECUTE FUNCTION documents.reject_published_version_edit();
