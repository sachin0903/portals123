-- 0152_dataset_catalogue: replace free-text SQL dataset authoring with a Graphene-authored
-- CATALOGUE of parameterised queries, and remove the two formats no firm can create.
--
-- WHAT CHANGES AND WHY
--
-- `documents.dataset_format` becomes ('catalogue', 'inline'):
--
--   * `sql` GOES. A firm could type any SELECT and the whole defence was a keyword denylist, a
--     hand-written lexer and a bind allowlist guarding a query nobody had reviewed. What a firm
--     actually needed was never "any query" — it was "the valuations table, for this client, over
--     this period" — so the query is ours now (`app/render/catalogue.py`) and the CHOICES are
--     theirs, carried in `catalogue_ref`. Removing the enum member is what closes the hole at the
--     database rather than only at the router: `scripts/seed_datasets.py` wrote such a row by
--     direct INSERT as an elevated role, and a router check would never have seen it.
--   * `csv` GOES because it was never reachable. The column, the FK and the CHECK arm all existed,
--     but no upload endpoint did, and the only two doors that mint a `documents.files` row sniff
--     content against an allowlist of pdf/png/jpeg/docx — a CSV is refused. Meanwhile the working
--     CSV import is client-side (`dataset-data-grid.svelte` parses the file and posts rows), which
--     lands as `inline`: a snapshot with no bucket, no cross-tenant FK, and columns the merge
--     picker can read. Finishing the server-side path would have built a worse second route to a
--     thing that already works — and `csv_file_id` carried a live cross-tenant read (the resolver's
--     join has no tenant predicate and the render worker runs as `service_role`), unreachable only
--     because the door was missing.
--
-- `liveness` GOES ENTIRELY, column and type. It was already derived from the format
-- (`_liveness_for`: sql ⇒ live, else static) rather than chosen, so it was a stored second source
-- of truth for a fact the format already carried — and with the formats above it would have
-- answered "static" for a catalogue entry, which is pulled fresh on every render.
--
-- `scope` STAYS. It is still meaningful for an `inline` dataset (always `global`), and for a
-- catalogue row it is written from the entry's own declared scope at save time so listing and
-- filtering agree with what will actually run. The ENTRY is authoritative at resolve time — a
-- stored column saying `global` for a per-client entry would be the mint-`contacts:read_all`
-- escalation `_execute_scoped` refuses to default into.
--
-- Greenfield, restructured in place on 0020's stated grounds: `documents.datasets` and
-- `documents.dataset_versions` are dev-seeded only and hold ZERO rows in every database (verified
-- on both local instances before writing this). A live table would need the expand/contract dance.
-- The guard below turns that premise into an assertion rather than an assumption: if any row DOES
-- carry a retired format, this migration fails loudly instead of deleting a firm's dataset.
--
-- One migration, not two: `ALTER TYPE ... ADD VALUE` cannot be used in the same transaction that
-- names the new value (the rule 0104 and 0109 both state), and every migration here runs inside
-- one transaction — so this SWAPS the type the way 0020 did rather than extending it.

-- FORCE ROW LEVEL SECURITY binds even the table owner (the migrator — it is NOT BYPASSRLS; see
-- 0001), so the backfill UPDATEs below would be RLS-filtered to zero rows while the DDL still sees
-- every physical row. Lift FORCE for the restructure, restore it at the end.
ALTER TABLE documents.datasets         NO FORCE ROW LEVEL SECURITY;
ALTER TABLE documents.dataset_versions NO FORCE ROW LEVEL SECURITY;

-- ── the premise, asserted ─────────────────────────────────────────────────────
DO $guard$
DECLARE
    n bigint;
BEGIN
    SELECT count(*) INTO n FROM documents.datasets WHERE format::text IN ('sql', 'csv');
    IF n > 0 THEN
        RAISE EXCEPTION
            'Refusing to migrate: % dataset(s) still use the retired sql/csv formats. '
            'This migration was written for a greenfield table (zero rows everywhere). '
            'Port them to a catalogue entry or an inline table first.', n;
    END IF;
END
$guard$;

CREATE TYPE documents.dataset_format_v2 AS ENUM ('catalogue', 'inline');

-- The CHECK names the old type's members, so it goes before the columns it constrains.
ALTER TABLE documents.dataset_versions DROP CONSTRAINT dataset_versions_source_ck;

-- ── datasets.format ──────────────────────────────────────────────────────────
ALTER TABLE documents.datasets ADD COLUMN format_v2 documents.dataset_format_v2;
UPDATE documents.datasets SET format_v2 = format::text::documents.dataset_format_v2;
ALTER TABLE documents.datasets DROP COLUMN format;
ALTER TABLE documents.datasets RENAME COLUMN format_v2 TO format;
ALTER TABLE documents.datasets ALTER COLUMN format SET NOT NULL;

-- ── dataset_versions.format ──────────────────────────────────────────────────
ALTER TABLE documents.dataset_versions ADD COLUMN format_v2 documents.dataset_format_v2;
UPDATE documents.dataset_versions SET format_v2 = format::text::documents.dataset_format_v2;
ALTER TABLE documents.dataset_versions DROP COLUMN format;
ALTER TABLE documents.dataset_versions RENAME COLUMN format_v2 TO format;
ALTER TABLE documents.dataset_versions ALTER COLUMN format SET NOT NULL;

DROP TYPE documents.dataset_format;
ALTER TYPE documents.dataset_format_v2 RENAME TO dataset_format;

-- ── liveness: a stored copy of what the format already says ──────────────────
ALTER TABLE documents.datasets DROP COLUMN liveness;
DROP TYPE documents.dataset_liveness;

-- ── the retired sources ──────────────────────────────────────────────────────
ALTER TABLE documents.dataset_versions DROP COLUMN sql_query;
ALTER TABLE documents.dataset_versions DROP COLUMN csv_file_id;

-- ── the catalogue reference ──────────────────────────────────────────────────
-- `{"key": "book.transactions", "params": {"date_from": "-12m", "date_to": "today"}}`.
--
-- Parameters are stored VERBATIM, which is the point: a date may be a relative token, and `-12m`
-- means "the last twelve months" resolved on every render. Storing the date it resolved to when
-- somebody filled the form in would freeze a quarterly statement on one fixed quarter for ever.
--
-- A jsonb blob rather than a parameters TABLE because the shape is owned by the entry's own
-- declaration in code: the accepted names, kinds, ranges and option values live there and are
-- validated on save (`catalogue.validate_params`) and again on compile. A table would be a second
-- schema for the same facts, needing a migration whenever an entry gains a setting.
ALTER TABLE documents.dataset_versions ADD COLUMN catalogue_ref jsonb;

-- Exactly one source per format, as before — two arms now instead of three.
ALTER TABLE documents.dataset_versions ADD CONSTRAINT dataset_versions_source_ck CHECK (
    (format = 'catalogue' AND catalogue_ref IS NOT NULL AND inline_data   IS NULL) OR
    (format = 'inline'    AND inline_data   IS NOT NULL AND catalogue_ref IS NULL)
);

-- A catalogue row must name an entry. Checked here as well as in the API because the API is not
-- the only writer — the dev seed script inserts directly as an elevated role, which is exactly how
-- the sql format outlived the router check that was supposed to have removed it.
ALTER TABLE documents.dataset_versions ADD CONSTRAINT dataset_versions_catalogue_key_ck CHECK (
    catalogue_ref IS NULL
    OR (jsonb_typeof(catalogue_ref -> 'key') = 'string' AND length(catalogue_ref ->> 'key') > 0)
);

ALTER TABLE documents.datasets         FORCE ROW LEVEL SECURITY;
ALTER TABLE documents.dataset_versions FORCE ROW LEVEL SECURITY;

COMMENT ON COLUMN documents.dataset_versions.catalogue_ref IS
    'For format=catalogue: {"key": <app/render/catalogue.py entry key>, "params": {...}}. '
    'Params are stored verbatim so a relative date range stays current on every render.';
