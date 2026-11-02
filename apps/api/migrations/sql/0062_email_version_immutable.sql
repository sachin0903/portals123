-- ============================================================================
-- 0062_email_version_immutable.sql — enforce the published EMAIL-version invariant.
--
-- 0042_email_templates.sql:37 states it as a bare comment ("Immutable once status = 'published'")
-- with no trigger and no CHECK behind it — exactly the gap 0032 closed for
-- documents.template_versions, in the schema that was cloned from it. `authenticated` holds UPDATE
-- on the table (0042:84) and `service_role` holds ALL (0042:85), so today a published email
-- version's content can be rewritten in place from either the firm router or the admin door.
--
-- Why it matters here specifically: `email.templates.current_published_version_id` is what the SEND
-- path resolves (app/email_render.py::render_published_email selects `v.subject`, `v.preheader`,
-- `v.schema` through that pointer), and publishing runs content gates — a subject must be present
-- and the body must render something visible (email_templates_service.py::_has_content). Editing the
-- published row in place walks straight around both: the next invite / password reset / task notice
-- a client receives would be the edited copy, with no version recorded and no gate re-run. The
-- versioned draft/publish machinery exists so that the live copy is a fixed, attributable artifact.
--
-- ── GUARDED: the content, and only the content ───────────────────────────────
--   * `schema`    — the editor project (html/css/gjs). What the body is rendered from.
--   * `subject`   — merged and sent verbatim.
--   * `preheader` — merged and sent verbatim (the hidden preview-text line).
-- Those are precisely the three columns `render_published_email` reads, i.e. everything that
-- determines what a client receives. There is no fourth.
--
-- ── DELIBERATELY UNGUARDED, and why the trigger would be WRONG to include them ──
-- The trigger keys on OLD.status = 'published' and never looks at NEW.status, so the state machine
-- keeps working:
--   * `status`        — draft → published passes because OLD.status is still 'draft' at that moment;
--                       published → archived (publish_version's "retire the previous live version"
--                       UPDATE) passes because no guarded column changes.
--   * `published_at`  \
--   * `published_by`  |— written BY the publish transition, on the row being published. An
--   * `authored_by_scope` /  idempotent re-publish of an already-published row writes exactly these
--                       three while leaving the content alone (OLD.status = 'published'), and must
--                       stay legal.
--   * `metadata`      — an unused annotation slot: nothing in app/ writes it and the send path never
--                       reads it. Guarding it would protect nothing today while pre-emptively
--                       forbidding the one plausible use of the column — a publish-time derivation
--                       written AFTER the status flip, which is exactly the trap 0032 documented for
--                       documents' input_schema / fixtures / input_form_snapshot.
--   * the identity/lineage columns (`uid`, `tenant_id`, `template_id`, `version_number`,
--                       `created_by`, `created_at`) — structural, not content. Nothing writes them
--                       after insert; if that ever changes it is a different invariant with a
--                       different remedy (and RLS already pins `tenant_id`).
--
-- The real write paths (app/email_templates_service.py — the ONE copy of this SQL, shared by the
-- firm router and the admin model door) are all compatible by construction, not by luck:
--   * `save_version` UPDATEs in place only while the head is a fresh DRAFT; once the head is
--     published it INSERTs v(n+1). It never touches a published row's content.
--   * `publish_version` transitions draft → published, so OLD.status = 'draft'.
--   * `restore_version` / `_insert_published` (load_defaults, clone_published) INSERT.
-- If this trigger ever breaks one of those, the trigger is wrong, not the service.
--
-- Fails with the same distinct SQLSTATE 0032 chose (`restrict_violation`, 23001 — psycopg maps it to
-- IntegrityError) so the API layer can map it to a 409 rather than a 500. Note that
-- `email_templates_service` catches IntegrityError only around the two template-metadata statements
-- (create_template / update_template), never around a version write, so an attempt surfaces as an
-- error instead of being mistranslated into DuplicateCode.
--
-- lock_timeout: CREATE TRIGGER takes a brief ACCESS EXCLUSIVE lock on the table — fail fast rather
-- than queue behind a long transaction (repo convention).
-- ============================================================================

SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION email.reject_published_version_edit() RETURNS trigger
  LANGUAGE plpgsql AS $fn$
BEGIN
  IF OLD.status = 'published'
     AND (NEW.schema    IS DISTINCT FROM OLD.schema
       OR NEW.subject   IS DISTINCT FROM OLD.subject
       OR NEW.preheader IS DISTINCT FROM OLD.preheader) THEN
    RAISE EXCEPTION
      'email template version % is published and its content is immutable', OLD.uid
      USING ERRCODE = 'restrict_violation',
            HINT = 'Save a new draft version instead of editing the published one.';
  END IF;
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS reject_published_edit ON email.template_versions;
CREATE TRIGGER reject_published_edit BEFORE UPDATE ON email.template_versions
  FOR EACH ROW EXECUTE FUNCTION email.reject_published_version_edit();
