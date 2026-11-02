-- 0019_template_form_snapshot: freeze the linked input form into a published template version.
--
-- A template links to an input form (documents.templates.input_form_code) — the questions asked
-- when generating from it. Editing that form must NOT retroactively change already-published
-- templates: a published version is a snapshot. On publish we capture the form's fields into
-- template_versions.input_form_snapshot; generation reads the snapshot, so the published template
-- is immune to later form edits until it is republished (which re-captures the current form).
--
-- Nullable: drafts have no snapshot, and versions published before this migration fall back to the
-- live form by code (see the /templates/{uid}/input-form resolution).

ALTER TABLE documents.template_versions ADD COLUMN input_form_snapshot jsonb;
