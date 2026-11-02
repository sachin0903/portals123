-- 0029_dataset_sample_data: preview-only sample rows for a dataset version.
--
-- The template editor's live PREVIEW needs representative rows. `inline` datasets already carry
-- their rows (inline_data); `sql`/`csv`/live datasets store none, so today the preview synthesises
-- junk from the column names. This adds an optional author-supplied sample: a
-- {"columns": [...], "rows": [[...]]} blob used ONLY for the editor preview (app/render/sample.py).
-- It is NEVER resolved at generate — real documents still run the live SQL / read the CSV / use the
-- inline rows — so sample data cannot affect a generated document or regeneration determinism.
--
-- Nullable and orthogonal to the exactly-one-source CHECK (it is not a source), so no constraint
-- change. Expand-only, backwards-compatible: existing versions keep NULL and fall back to synthesis.

ALTER TABLE documents.dataset_versions ADD COLUMN sample_data jsonb;
