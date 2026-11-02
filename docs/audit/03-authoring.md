# 3 · Authoring — templates, editor, render pipeline, forms, datasets

Portal: **platform**. Part of the [functional audit](README.md) — statuses: ✅ working · ⚠️ partial · 🎭 demo/mock · ❌ missing · 💥 broken

---

Traced: `apps/platform/src/routes/{templates,forms,datasets,documents}`, `apps/platform/src/lib/components/{templates,forms,datasets}`, `packages/ui/src/lib/{templates,forms}`, `apps/api/app/routers/{templates,forms,datasets,documents}.py`, `apps/api/app/render/*`, `apps/api/app/worker.py`, `apps/api/migrations/{versions,sql}`.

---

## A. TEMPLATES — LIST & DETAIL

### Create template

- **Status**: ✅
- **What it does today**: A 4-step wizard (`packages/ui/src/lib/templates/create-template-dialog.svelte:50-120`) collects type → name/code/renderer → input form → datasets, posts to `?/create` (`apps/platform/src/routes/templates/+page.server.ts:42-69`) → `POST /templates` (`apps/api/app/routers/templates.py:188-236`), which inserts the template plus an empty draft `v1` in one transaction and 409s on a duplicate code. The page then `invalidateAll()`s and jumps straight into the editor (`+page.svelte:45-60`).
- **Expected**: As an adviser I create a template and land in the editor with my chosen form/datasets already attached. As an ops lead I cannot create two templates sharing a code.
  - Code is unique per firm, validated client- and server-side.
  - Initial draft v1 exists immediately.
  - Chosen `input_form_code` + `linked_datasets` persist on the record.
  - Renderer choice persists on v1.
  - Duplicate code surfaces a readable error, not a 500.
- **Gaps**:
  - Wizard offers a `renderer` choice on the _template_, but renderer is a version property; on later PATCH it silently lands on whatever the latest version is (see _Publish/version lifecycle_).
  - No validation that the picked form is _published_ — a draft or archived form can be linked (`form-link-dialog.svelte:60-73` only filters, never enforces).

### Templates list (filter / search)

- **Status**: ⚠️
- **What it does today**: `GET /templates` returns non-archived templates with version counts and latest-version status (`templates.py:169-185`); the page filters client-side by type/status/search (`+page.svelte:92-104`). The row overflow menu offers View, Edit, and Generate document (disabled unless `currentVersionStatus === 'published'`, `+page.svelte:249-258`).
- **Expected**: As an adviser I find a template by name/code and see at a glance whether it is publishable/generatable.
  - Filters return what they claim.
  - A failed API load is distinguishable from "no templates".
  - Row actions reflect the record's real state.
- **Gaps**:
  - The **"Archived" status filter is dead**: `list_templates` hard-excludes `archived_at IS NOT NULL` (`templates.py:179`), so `statusFilter === 'archived'` can never match anything. All the `isArchived` styling (`+page.svelte:175-270`) is unreachable code.
  - `loadError` is computed (`+page.server.ts:30`) and never rendered — an API failure shows an empty list with no banner, contrary to the code comment. (`forms/+page.svelte:179` does it correctly.)
  - **The disabled menu item is "Generate document", not Archive** — there is no Archive item at all (see below). Its disabled state is correct behaviour.

### Edit template metadata (Details Sheet)

- **Status**: ⚠️
- **What it does today**: The Sheet edits name, document type, renderer and description (`templates/[id]/+page.svelte:549-621`) → `?/updateDetails` → `PATCH /templates/{uid}` (`templates.py:253-311`). `code` is patchable by the API but **not exposed** in the Sheet.
- **Expected**: As an ops lead I correct a template's name/type/description without touching its content or version history.
  - Only metadata changes; content and version statuses are untouched.
  - Clearing the description works (`''` → null-ish).
  - Renderer changes never mutate a published, immutable version.
- **Gaps**:
  - 💥 **Renderer PATCH breaks publish immutability**: `templates.py:296-305` writes `renderer` to `version_number = max(...)` _regardless of status_. If the latest version is `published`, editing Details silently rewrites a published version's render engine — changing the output of a version that the UI promises is immutable (`[id]/+page.svelte:545`).
  - `code` is editable via the API but not the UI; renaming a code would also break `input_form_code`-style code references elsewhere (no FK).

### Publish version / version lifecycle & immutability

- **Status**: ⚠️
- **What it does today**: `POST /templates/{uid}/versions/{vid}/publish` (`templates.py:856-919`) requires `status='draft'`, requires content (`_has_content`, accepting both the GrapesJS blob and a legacy shape, `:820-846`), archives the prior published version, stamps `published_at/by`, points `templates.current_published_version_id` at it, and freezes the linked form onto the version (`_capture_form_snapshot`, `:716-753`). Unknown variables come back as a **non-blocking** warning rendered as an Alert (`[id]/+page.svelte:402-428`).
- **Expected**: As a compliance owner, a published version is a frozen artefact: its content, form snapshot and renderer never change, and generated documents are reproducible.
  - Only drafts publish; publishing twice 409s.
  - Empty templates are refused.
  - The linked form is snapshotted at publish.
  - A published version's schema/renderer is immutable at the DB level.
  - Publishing warns (not blocks) on unsourced tokens.
- **Gaps**:
  - **No DB-level immutability**: `0012_templates.sql:43` only _comments_ "Immutable once status = 'published'". There is no trigger or `CHECK`; `authenticated` holds `UPDATE` on `template_versions` (`:142`), and the renderer PATCH above proves the gap is reachable.
  - **Nothing validates Jinja syntax at publish.** `_has_content` counts blocks only. A broken `{% for %}` publishes cleanly and only fails later, per-document, in the worker.
  - No unpublish / rollback-to-version endpoint: "restoring" an old version means copying it into a new draft (see _Version history restore_).
  - `linked_datasets` is not snapshotted at publish (only the form is), so deleting or re-versioning a dataset silently changes a "frozen" template's output.

### Link input form

- **Status**: ✅
- **What it does today**: `FormLinkDialog` writes a form **code** through `?/setInputForm` → `PATCH {input_form_code}` (`[id]/+page.server.ts:101-104`), with explicit set-vs-clear handling server-side (`templates.py:272,282`). `GET /templates/{uid}/input-form` (`:625-670`) resolves the fields the generate dialog asks for, preferring the published snapshot over the live form and reporting `source: snapshot|live|none`.
- **Expected**: As an adviser I attach a fact-find to a template and the generate dialog asks exactly those questions; later form edits don't change already-published templates.
  - Link and unlink both persist.
  - Published templates read the frozen snapshot.
  - Pre-snapshot versions fall back to the live form.
  - The editor's variable picker offers the linked form's field keys.
- **Gaps**:
  - The link is a **bare code string with no FK**. `PATCH /forms/{uid}` allows changing `code` (`forms.py:247`), which silently orphans every template pointing at the old code — the template then reports `source: none` and generates with no answers.
  - Draft/archived forms can be linked; nothing warns.

### Link datasets

- **Status**: ⚠️
- **What it does today**: `DatasetLinkDialog` toggles codes into `linked_datasets` via `?/setDatasets` (`[id]/+page.server.ts:95-98`); disabled datasets are non-selectable (`dataset-link-dialog.svelte:117-127`). At render, the worker unions three sources: `t.linked_datasets`, chart/table component bindings, and bare `{% for x in code %}` loop sources (`worker.py:132-137`).
- **Expected**: As an adviser I declare which data a template draws on, and the render resolves exactly those.
  - Links persist as codes and drive resolution.
  - Component/loop bindings are resolved even when not declared.
  - Removing a link that a chart still binds is flagged.
- **Gaps**:
  - `linked_datasets` is **advisory only** — a chart or `{% for %}` resolves regardless, so the panel gives a false sense of "this is the data contract".
  - No reverse guard: nothing stops unlinking (or deleting) a dataset a chart still binds; the chart just renders blank.
  - Dataset codes are strings with no FK; `documents.datasets` has no code-rename path, which is the only reason this holds.

### Archive template

- **Status**: ❌ (endpoint exists, no UI)
- **What it does today**: `DELETE /templates/{uid}` is a **soft archive** — sets `archived_at`, keeps versions, and is idempotent (`templates.py:314-342`). Nothing in the platform app ever calls it: no action in `templates/+page.server.ts` or `templates/[id]/+page.server.ts`, and no Archive menu item anywhere (`grep Archive apps/platform/src/routes/templates` returns only styling/labels).
- **Expected**: As an ops lead I retire a superseded template so nobody generates from it, while keeping its history for audit.
  - Archive from the list row menu and the detail page, behind a confirm.
  - Archived templates leave the default list and appear under the Archived filter.
  - Generation from an archived template is refused.
  - Unarchive restores it as a draft-publishable record.
- **Gaps**:
  - No UI whatsoever; the "Archived" filter and all `isArchived` rendering are unreachable.
  - **No unarchive endpoint** — archiving via the API is a one-way door (contrast `POST /forms/{uid}/unarchive`).
  - 💥 **An archived template can still be generated from**: `generate_document`'s lookup has no `archived_at IS NULL` filter (`documents.py:352-367`), and `GET /templates/{uid}` doesn't either, so a bookmarked `/documents/create?template=…` still works.
  - No in-use guard: archiving does not consider flows/journeys or scheduled generation referencing the template.

### Duplicate / delete template

- **Status**: ❌
- **What it does today**: Neither exists. There is no `POST /templates/{uid}/duplicate` and no hard `DELETE` (the `DELETE` verb is the soft archive above). The only "copy" primitive is per-page duplication inside the editor (`TemplateEditor.svelte:1559-1563`) and the JSON export/import round-trip (`:353-406`).
- **Expected**: As an adviser I clone last year's suitability template as the starting point for this year's. As an ops lead I permanently remove a template created in error.
  - Duplicate creates a new template + draft v1 carrying the source's content, form link and dataset links, with a derived unique code.
  - Duplicate never copies published status.
  - Hard delete is available only for templates with zero generated documents.
- **Gaps**:
  - Cloning today requires Export → New template → Import, which loses the form link (import only restores `gjs_project`, `variable_schema`, `datasets` — `TemplateEditor.svelte:392-399`).
  - No way to remove a mistaken template at all; it lingers in the list forever (archive isn't even wired).

### Template detail preview + version history

- **Status**: ⚠️
- **What it does today**: `GET /templates/{uid}/preview` returns the latest version's saved HTML/CSS wrapped in the renderer's base CSS (`templates.py:441-473` → `renderers.py:preview_html_document`), shown in a sandboxed, scaled iframe (`[id]/+page.svelte:436-451`). History lists **published milestones only** (`:100`, `versions.filter(v => v.publishedAt)`).
- **Expected**: As an adviser I see what the template looks like and which versions went live, and can open any of them.
  - Preview reflects the latest saved content.
  - History lists every published version with actor + date.
  - I can open/compare/restore a listed version.
- **Gaps**:
  - History is read-only: no link to view a past version, no diff, no restore from this page (all of that lives only inside the editor).
  - Preview is layout-only (Jinja tokens visible) with no affordance explaining that; the data-filled preview exists but only inside the editor.
  - Draft versions never appear in history, so "v3 draft in progress" is invisible here.

---

## B. TEMPLATE EDITOR (`TemplateEditor.svelte`, 4,851 lines)

### Block palette & canvas editing

- **Status**: ✅
- **What it does today**: A real GrapesJS instance (`editor/create.ts`, 1,600 lines) with a bespoke palette (`editor/blocks.ts:41-205`): Cover, Label+Value, bullet/numbered lists, Divider, Note/Highlight callouts, Columns/Full-width/30-70 layouts, Chart, Text table, Data table, Repeat, Conditional — plus Text/Image from `grapesjs-blocks-basic`. Custom component types (`editor/component-types.ts`, 1,900 lines) give each a typed identity, traits and canvas rendering. Right-click inserts blocks, duplicates and deletes (`TemplateEditor.svelte:1713-1776`).
- **Expected**: As an adviser I compose a document out of typed blocks, edit copy inline, and the saved HTML matches what I see.
  - Every palette block drops, selects, styles and serialises.
  - Text is RTE-editable inline.
  - Blocks survive a save/reload round-trip.
- **Gaps**:
  - The **Cover block is a plain flex div** (`blocks.ts:48-55`) with `min-height:60vh` — `vh` is meaningless in WeasyPrint paged media, so a cover that fills the page in the canvas will not in the PDF.
  - The **Conditional block is inert as authored**: its `data-condition` starts empty and serialisation only wraps elements carrying `data-comp-cond` (`serialize.ts:30-54`) — a different attribute. Dropping a Conditional and setting its trait produces no `{% if %}` unless the trait writes `data-comp-cond` (it writes `data-condition`).
  - The **Repeat block's placeholder text ships into the document** ("Drop content here — use `{{ item.field }}`"), and its `data-loop-key` is empty by default, so an unconfigured Repeat renders its instructions into the client's PDF.

### Chart block + data binding (dataset vs manual)

- **Status**: ✅
- **What it does today**: The config modal writes ~19 `data-comp-*` attributes in one undo step (`TemplateEditor.svelte:1963-1973`). Dataset mode resolves **real rows** through `/templates/[id]/edit/dataset-data` → `GET /datasets/{uid}/data` and draws the same SVG the server will (`:2095-2135`, `editor/chart-preview.ts`), with a client subject picker for subject-scoped datasets and a "missing fields" warning (`:2086-2093`). Manual mode carries its own `{columns, rows}` on the component (`:2040-2044`), which `serialize.ts:82-98` promotes to `binding._inline_rows` and **blanks the dataset binding**, so a stale binding cannot resolve the wrong side. At render, `components.py:substitute_components` swaps `<g-chart>` markers for server SVG.
- **Expected**: As an adviser I bind a chart to a dataset column, see the real rows, and get the same chart in the PDF.
  - Field pickers offer the dataset's actual columns.
  - Preview uses the identical resolver the worker uses.
  - Manual rows work with no dataset at all.
  - Mode switches never leave a half-bound component.
- **Gaps**:
  - Field pickers are empty whenever the column schema is unknown — `SELECT *` SQL datasets return `[]` from `_columns_from_sql` (`datasets.py:147`), so the whole picker degrades to nothing with no explanation.
  - `data-comp-colors` and the tenant palette are resolved from live CSS vars in the browser (`:2171-2184`); the server chart renderer has no access to tenant branding, so PDF chart colours only match if the author explicitly pinned hexes.
  - Chart width/height come from marker attributes defaulting to 600×340 (`components.py:98-100`) — independent of the styled frame size in the canvas, so canvas/PDF sizing can diverge.

### Data table (Jinja table) & text table

- **Status**: ✅
- **What it does today**: `doc-jtable` builds a genuine `{% for row in code %}` loop from a visual column editor (dataset picker, drag-reorder columns, per-column expression + Jinja filter chain — `TemplateEditor.svelte:798-925`), or raw Jinja/HTML with a balance lint (`editor/jinja-table-lint.ts`). Column pickers use the dataset's real columns (`:2010`). `doc-texttable` is a static typed grid. Table print CSS is appended at save when used (`serialize.ts:255`).
- **Expected**: As an adviser I loop a dataset into a table with formatted columns and it renders identically in the PDF.
  - Columns bind to real dataset fields.
  - Filters (`| currency`, `| date`) render server-side.
  - The loop's dataset is resolved at render even though it carries no component.
  - Malformed raw Jinja is flagged before save.
- **Gaps**:
  - The lint is brace-counting only (`jinja-table-lint.ts:9-38`) — it cannot catch `{% for %}` over a non-existent dataset, an undefined filter, or a mismatched `{% else %}`.
  - Loop-source resolution is heuristic: `loop_sources` (`components.py:_FOR_LOOP`) treats **any** bare identifier as a candidate dataset code; a typo resolves to `[]` and the table renders empty with no error anywhere.
  - `DOC_JTABLE_CSS` is only shipped when the string `doc-jtable-inner` appears in the final HTML (`serialize.ts:255`) — a fragile substring contract.

### Variable insertion + live lint

- **Status**: ✅
- **What it does today**: A curated catalogue (12 core client/adviser/firm/system tokens + the linked form's field keys + **per-dataset `row.<col>` groups**) is pushed into the canvas iframe for `{{`-triggered autocomplete with filter suggestions (`TemplateEditor.svelte:2223-2269, 2305-2329`). A debounced lint serialises the document and reports unsourced tokens, and is **dataset-column aware**: for a single-target loop over a dataset with a known schema it flags `row.typo` (`editor/variables.ts:47-103`, tested in `variables.test.ts`). The count shows in the toolbar with a hover list (`:2772-2782`). The server mirror (`templates.py:_lint_unknowns`) adds "did you mean" suggestions and is shared by preview, publish and the generate gate — with a test asserting the catalogues cannot diverge (`test_templates_api.py:276`).
- **Expected**: As an adviser I insert canonical variables rather than typing tokens, and I am told immediately when a token has no source.
  - Catalogue covers context + form fields + dataset columns.
  - Lint updates as I type, without stealing the caret.
  - Client lint and server gate agree.
  - Column-level typos in dataset loops are caught.
- **Gaps**:
  - The toolbar indicator is a **`title` tooltip only** — no click-through to the offending element, no inline canvas marking, so on a long document you cannot find the bad token.
  - Dataset codes are auto-added to the "known" set from links/bindings/loop sources (`templates.py:437`, `variables.ts:68`), so a token referencing a **deleted or disabled** dataset is never flagged — the exact case that renders blank.
  - Column-awareness only fires for single-target loops over datasets with a parsed schema; `SELECT *` datasets and tuple-target loops are silently unchecked.

### Save / flush pipeline

- **Status**: ⚠️
- **What it does today**: `save()` builds the render-ready schema (`serialize.ts:buildSchema`) and hands the blob to `onSave` (`TemplateEditor.svelte:1403-1432`); the page debounces 600 ms then POSTs `?/saveVersion` → `POST /templates/{uid}/versions` (`edit/+page.svelte:17-47`, `edit/+page.server.ts:126-146`). The API updates the latest version in place while it is a draft, else opens `v(n+1)` (`templates.py:756-817`). Failures toast "Save failed — your latest changes are not stored."
- **Expected**: As an adviser my work is never silently lost, and a save produces exactly one draft version.
  - Explicit Save persists deterministically.
  - Navigating away with unsaved work warns me.
  - Draft saves do not spawn a version per keystroke.
  - A failed save is loud and recoverable.
- **Gaps**:
  - **There is no autosave** — despite `templates.py:763` describing "the autosave loop". No `setInterval`, no `Ctrl+S` binding, no dirty flag. The only trigger is the toolbar button (`:2467`).
  - **No unsaved-changes guard.** `beforeNavigate` fires `void flush()` without cancelling the navigation (`edit/+page.svelte:44-47`) — the fetch races the unmount and there is no `beforeunload` handler, so closing the tab or a fast client-side nav loses work. (The _form_ builder does this correctly: `forms/[id]/edit/+page.svelte:64-69`.)
  - **The localStorage draft cache is dead code**: `loadStored`/`saveStored` short-circuit unless `storageKey` is set (`:328-350`), and the edit page never passes it — so the advertised offline recovery does nothing.
  - `templateId` and `description` are declared as props (`:110-113`) and passed by the page but never destructured — silently ignored.
  - After a save the page does not `invalidate`, so the History panel's `versions` prop goes stale until a full reload.

### Version history: preview, switch, diff, restore

- **Status**: ⚠️
- **What it does today**: The History dropdown lists real server versions (`:2844-2880`). Previewing fetches that version's blob on demand via `/templates/[id]/versions/[vid]` and `loadProjectData`s it into the canvas, keeping the working state as `priorBlob` (`:1448-1463`). A component-id diff then outlines components green (would be restored) / amber (changed since) with counts (`:219-326`). Cancel restores the prior blob exactly; Restore keeps the loaded version and calls `save()` (`:1473-1492`).
- **Expected**: As an adviser I inspect a past version in place, see what would change, and adopt it as a new draft without losing my current work.
  - Preview is non-destructive and reversible.
  - The diff is meaningful.
  - Restore creates a new draft; history is never rewritten.
- **Gaps**:
  - The diff is **inline-outline only, injected 400 ms after load on a timer** (`:318-326`) — no diff list, no per-change navigation, and it silently produces nothing if GrapesJS is slower than the timeout.
  - Diff keys on GrapesJS component ids, which are not stable across an import or a `loadProjectData` round-trip, so a legitimately unchanged document can report every component as changed.
  - `restoreVersion()` calls `save()` with **no confirmation** and no note of provenance — the new draft doesn't record "restored from v2".
  - Cancel is only reachable while the preview banner is open; a page reload mid-preview loses `priorBlob` and leaves the canvas showing the old version as if it were current (it is unsaved, but nothing says so).

### Import / export JSON

- **Status**: ⚠️
- **What it does today**: Export builds `{_graphene_template: '1', name, documentType, schema:{html, css, datasets, pageSize, components, gjs_project, variable_schema}}` and downloads it (`:353-379`). Import validates the marker, then `loadProjectData`s `gjs_project` and restores `variable_schema` + `datasets` (`:381-406`).
- **Expected**: As an ops lead I export a template for backup/transfer and re-import it into another firm.
  - Round-trip preserves content, page setup, header/footer and component configs.
  - A foreign/corrupt file is rejected with a clear message.
- **Gaps**:
  - **Import loses page layout** — `layout` (paper size/orientation/margins) is exported nowhere and never restored, so an imported A3 landscape template comes back as the default A4 portrait until re-set.
  - Import does not restore `html`/`css`/`components`/`pageSize`; it relies wholly on `gjs_project`, so a file exported from a different editor version (or hand-edited) silently loses charts/tables.
  - Import replaces the document with **no confirmation** and no undo checkpoint, and does not save — one mis-click plus a save destroys the draft.
  - No version/schema migration on `_graphene_template: '1'` beyond the equality check.

### Page setup (size / orientation / margins)

- **Status**: ✅
- **What it does today**: Selecting a page surfaces bespoke controls that write component props (`:512-620`), including draggable margin handles. Each page keeps its **own** geometry, and `bakePerPageGeometry` (`serialize.ts:186-209`) emits `@page tmplpgN` + `.page.tmpl-page-N` rules so WeasyPrint switches geometry per page while browsers honour the width/min-height. "Apply to all pages" copies setup + background/border (`:1568-1596`).
- **Expected**: As an adviser I set A4 portrait with 20 mm margins, make page 3 landscape, and the PDF matches.
  - Per-page geometry survives to the PDF.
  - Apply-to-all is one undo step.
  - The canvas preview matches the printed page.
- **Gaps**:
  - Page-one geometry is also written as the top-level `pageSize` fallback (`serialize.ts:259`); if the document has no `.page` components the panel `layout` is used instead — two sources of truth that can disagree.
  - Margins are stored as unitless numbers assumed to be mm with a magic default of 19 (`:519-523`) — no unit choice, no validation against printer-safe minimums.

### Header / footer

- **Status**: ⚠️
- **What it does today**: `schema.headers/footers` are applied to the canvas by an effect with a signature check to avoid feedback loops (`:1391-1400`), "apply to all" copies them (`:1597-1615`), and the renderer's base CSS absolutely positions `.tmpl-header/.tmpl-footer` at the page edges with three-cell left/centre/right layout (`renderers.py:_BASE_CSS_STATIC`).
- **Expected**: As an adviser I put the client name and page number in a running header/footer on every page.
  - Header/footer repeats on every page of the PDF.
  - Page numbers resolve.
  - Merge tokens in the header resolve.
- **Gaps**:
  - Headers/footers are **per-`.page` absolutely-positioned divs, not CSS running elements** — so if a page's content overflows onto a second physical sheet, that sheet has no header/footer.
  - Page numbering relies on `._wpc-page::before { content: counter(page) }` (`renderers.py`), which is WeasyPrint-specific; the Chromium renderer ignores the injected base CSS entirely (`PlaywrightRenderer._render_sync` passes only the template CSS), so page numbers and header geometry are wrong under `renderer='chromium'`.

### CSS mode

- **Status**: ✅
- **What it does today**: Toggling swaps the visual Style manager for a textarea holding the selected element's own declarations; Apply parses them paren/quote-aware, reports every malformed declaration and **skips the whole apply** rather than partially writing (`:947-978`, `editor/css-validate.ts`, with tests). Leaving CSS mode commits first.
- **Expected**: As a power user I hand-write CSS for one element and the visual controls reflect it afterwards.
  - Invalid CSS is refused with per-declaration errors.
  - Apply is a single undo step.
  - Round-trips with the visual controls.
- **Gaps**:
  - Scoped to the **selected element's inline rule only** — no document-level stylesheet editing, so shared classes/`@media print` rules cannot be authored at all.
  - No property validation: `position: fixed`, `vh` units or unsupported properties are accepted and silently break the PDF.

### Preview mode (data-filled) & WeasyPrint parity

- **Status**: ⚠️
- **What it does today**: Preview POSTs the **current, unsaved** schema to `/templates/[id]/edit/preview` → `POST /templates/{uid}/preview` (`templates.py:483-536`), which renders it through the **real pipeline** against sample data — fake "John Doe" client, per-type sample per form field, and either the author's `sample_data` or rows synthesised from the column schema (`render/sample.py`) — then substitutes chart/table markers server-side and wraps it in the renderer's base CSS. The iframe is measured and zoom-scaled (`:1852-1859`); unknown variables show a dismissable banner.
- **Expected**: As an adviser the preview is a faithful dress rehearsal of the generated PDF.
  - Charts, tables and merge tokens render as they will.
  - The preview never runs live SQL or touches real client data.
  - Layout matches WeasyPrint's pagination.
- **Gaps**:
  - **HTML-in-a-browser is not WeasyPrint.** The preview is Chrome-rendered; pagination, `page-break` behaviour, table splitting, font metrics (WeasyPrint pins Liberation Sans, `renderers.py:_PINNED_FONT_FAMILY`) and `@page` handling all differ. There is no PDF preview anywhere in the authoring flow — the first true render is a generated client document.
  - **Jinja/render errors surface as one generic string.** `render_template_preview` calls `jinja_engine.render` with no try/except, so a `TemplateSyntaxError` is an unhandled 500 → the SvelteKit endpoint errors → the editor shows "Could not render the preview." No line, no token, no cause.
  - Preview always uses **sample** data with no option to preview against a real client, even though the chart config panel already has a client picker and a live resolver.

### Undo / redo + undo history menu

- **Status**: ✅
- **What it does today**: GrapesJS UndoManager wired to buttons plus a dropdown listing grouped, still-undoable steps with inferred labels ("Add element", "Style change", "Text edit"), and undo-to-a-point (`:1498-1546`). Box-model drags are collapsed into one step (`:1022-1024`).
- **Expected**: As an adviser I undo a mis-drag in one step and can see what I am undoing.
- **Gaps**:
  - Undo-only; there is no redo history list, and `undoTo` loops `um.undo()` without bounds-checking against a stack mutated meanwhile.
  - Labels are heuristics off the raw action payload — many real edits read as a bare "Edit".

### Theme panel

- **Status**: 🎭
- **What it does today**: A full brand/chart-colour swatch dropdown exists but is wrapped in `{#if false}` (`:2784-2843`) with hard-coded hexes and a "Change in Settings →" button that does nothing. The live tenant palette used by charts is resolved separately from CSS vars (`:2171-2184`).
- **Expected**: As an ops lead the editor shows the firm's brand palette and links to branding settings.
- **Gaps**: ~60 lines of dead UI plus two unused constants (`themeBrand`, `themeChartColours`) — should be deleted or wired to `/branding`.

### Editor datasets panel

- **Status**: 🎭
- **What it does today**: A "Datasets" accordion with add/remove and dataset names sits inside a `<div class="hidden">` (`:3838-3900`), explicitly "kept mounted for chart/table binding". Its state (`linkedDatasets`) is persisted into the version blob (`:1413`) but the worker never reads it — resolution uses `templates.linked_datasets`, component bindings and loop sources (`worker.py:132-137`).
- **Expected**: Either the editor manages dataset links (and they persist to the template record), or the panel does not exist.
- **Gaps**: Dead UI whose state is nonetheless serialised into every saved version — misleading to anyone reading the persisted schema.

---

## C. RENDER PIPELINE

### Generate a document (`POST /documents/generate`)

- **Status**: ✅
- **What it does today**: Resolves the template's `current_published_version_id`, 404s if unpublished, 422s if the version has no `html`, validates the target contact is visible, runs the **completeness gate** (`unresolved_variables` — the same lint as preview/publish) and 422s with the offending token names, then inserts a `queued` document + a `render_jobs` row + a `document.queued` audit event in one transaction (`documents.py:334-443`). The UI polls `/documents/[id]/status` every interval until `generated`/`failed` (`documents/create/+page.svelte:9-40`).
- **Expected**: As an adviser I generate from a published template, am told immediately if it cannot fill, and get the PDF or a clear failure.
  - Only published versions generate.
  - Unsourced variables block by default.
  - The document is invisible to the client until explicitly sent.
  - The queue insert is atomic with the document insert.
- **Gaps**:
  - **`allow_incomplete` is unreachable from the UI** (`documents.py:331`, grep finds no caller) — there is no "generate anyway" escape hatch for a deliberate draft, so any lint false positive is a hard block.
  - **Archived templates still generate** (no `archived_at` filter, `:352-367`).
  - The gate checks token _names_ only. A template whose dataset was deleted, disabled or emptied passes the gate and produces a document full of blank tables.
  - No idempotency key: double-submitting queues two documents.

### DocumentWorker render → storage → generated

- **Status**: ✅
- **What it does today**: Three-phase loop so no pooled connection is held during the multi-second render (`worker.py:290-317`): claim with `FOR UPDATE SKIP LOCKED` (also reclaiming jobs stuck `processing` past a 5-minute visibility timeout, `:44-67`); load document + published version + client context + resolved datasets (`:100-138`); render Jinja → substitute `<g-chart>/<g-table>` → WeasyPrint/Chromium → S3 under a deterministic key; then persist `files` + `documents.status='generated'` + `dataset_snapshots` + a `document.generated` audit row in a short, **idempotent** transaction guarded on `(status='processing' AND attempts=unchanged)` (`:178-251`).
- **Expected**: As a firm, generation is reliable, reproducible and auditable.
  - No duplicate files/audit rows under reclaim.
  - The exact dataset rows used are snapshotted.
  - Renderer version is recorded.
  - A crashed worker's job is recovered.
- **Gaps**:
  - `dataset_snapshots` records the rows but **not the dataset version numbers**, so a re-render after a dataset re-version isn't provably reproducible.
  - The S3 key is `(tenant, doc_uid, version_number)` — a re-render of the same document overwrites the previous PDF with no history.
  - Single-process worker also drains the flows outbox and task notifier (`:320-355`); a slow WeasyPrint render blocks flow dispatch behind it.
  - No metrics/heartbeat; a dead worker is only visible as documents stuck `queued` forever.

### Retry / dead-letter

- **Status**: ⚠️
- **What it does today**: Any exception requeues with a flat 30 s backoff, or after `max_attempts` (default 3, `0013_render_jobs.sql`) marks the job `failed` and the document `failed` with the error text in `documents.error` and `render_jobs.last_error` (`worker.py:254-287`).
- **Expected**: As an ops lead I see failed renders, know why, and can retry after fixing the template.
  - Transient failures retry; permanent ones dead-letter.
  - The failure reason is retrievable.
  - Retry is possible without re-entering the whole generate flow.
- **Gaps**:
  - **No retry/regenerate UI anywhere** (grep for retry/regenerate in `routes/documents` → nothing). A dead-lettered document is terminal; the adviser must generate a fresh one.
  - **No dead-letter queue view.** `render_jobs` has a read policy for `authenticated` (`0013_render_jobs.sql`) but no endpoint and no admin screen — `last_error` is write-only in practice.
  - Retries are indiscriminate: a `TemplateSyntaxError` (permanently fatal) burns all three attempts on a 30 s backoff exactly like an S3 blip.
  - Fixed backoff, no jitter, no cap on concurrent failures.

### Failure surfacing to the user

- **Status**: 💥
- **What it does today**: The worker writes a real diagnostic (`f"{type(exc).__name__}: {exc}"`) into `documents.documents.error` (`worker.py:269`). `DocumentOut` (`documents.py:75-108`) **does not include an `error` field**, and `/documents/[id]/status/+server.ts` returns `{status}` only. The generate dialog therefore shows the constant `'Rendering failed. Check the template and try again.'` (`documents/create/+page.svelte:38`); the document detail page shows only a red "Failed" badge.
- **Expected**: As an adviser, when a render fails I am told what to fix.
  - The failure reason (or a mapped, safe summary) reaches the UI.
  - The failing template/version is identified.
  - Ops can see the raw error.
- **Gaps**:
  - The single most useful diagnostic in the system is captured and then discarded at the API boundary.
  - No distinction between "template broken", "dataset unavailable" and "renderer crashed" — all identical to the user.
  - Raw `str(exc)` would need sanitising before exposure (it can contain SQL from a dataset query), so the fix is a mapped/redacted field, not a raw passthrough.

### Dataset resolution at render

- **Status**: ⚠️
- **What it does today**: `resolve_datasets` (`render/resolve.py:153-181`) takes the union of declared links, component bindings and loop sources, and for each code materialises the latest published (else latest) version of an **enabled** dataset: `inline` → stored rows, `sql` → the stored SELECT with `:subject_id` bound, `csv` → the uploaded file parsed. Every failure is caught and yields `[]` "so one bad dataset can't blank a whole document".
- **Expected**: As an adviser the data in my document is the firm's current data, and a data problem is visible.
  - Each code resolves to rows.
  - Subject-scoped datasets bind the document's recipient.
  - A dataset that cannot resolve is reported, not swallowed.
- **Gaps**:
  - **Silent-empty is the core failure mode**: a deleted, disabled, mis-typed, unpublished or erroring dataset all resolve to `[]` (`:159-181`), producing a plausible-looking client document with empty charts and tables and **no warning at any layer** — not the gate, not the worker, not the document record. Only a `logger.warning` on the worker's stdout.
  - **A template referencing a dataset the tenant lacks is never detected**: the lint adds every referenced code to the "known" set, and resolution returns `[]`, so this passes gate, publish and render.
  - `_latest_version` falls back to the latest _draft_ version when nothing is published (`:95`) — an unfinished dataset edit can reach a client PDF.
  - No row cap: a large SQL result is materialised into memory and into `dataset_snapshots` jsonb.

### Renderer selection (WeasyPrint vs Chromium)

- **Status**: ⚠️
- **What it does today**: Per-version `renderer` enum, overridable per document via `metadata.renderer` (`worker.py:169-174`); `get_renderer` returns `WeasyPrintRenderer` (default) or `PlaywrightRenderer` (`renderers.py`). Both are lazily imported so the API never needs the native stack.
- **Expected**: As an ops lead I pick the engine that renders a given template best, and both produce equivalent output.
- **Gaps**:
  - **Chromium is not at parity**: it ignores `page_size` _and_ `branding_css`, and receives only the template CSS — none of `_BASE_CSS_STATIC`, `_make_page_css` or the per-page `@page` rules. So header/footer positioning, page counters, `.page` padding and any non-A4 geometry are simply wrong. It is presented in the Details Sheet as a peer option with no warning (`[id]/+page.svelte:593-603`).
  - Chromium launches a browser per render — no pooling.
  - Choosing the renderer mutates a published version (see _Edit template metadata_).

---

## D. FORMS

### Create form

- **Status**: ✅
- **What it does today**: A Sheet collects name, code, entity type, document type and description → `?/create` → `POST /forms` (`forms/+page.server.ts:31-56`, `forms.py:176-214`), inserting a `draft` and 409ing on a duplicate code; the page jumps into the builder.
- **Expected**: As an adviser I create a form and start adding fields immediately, with a code unique to my firm.
  - Draft status on create; code unique.
  - Entity type recorded.
  - Duplicate code shows a readable error.
- **Gaps**: `questionnaire_dataset_code` exists on the model, in `CreateForm`/`UpdateForm` and in the TS types (`lib/forms.ts:18`) but has **no UI anywhere** — a dead field that also implies an unbuilt "form answers become a dataset" feature.

### Form builder (fields, sections, ordering, widths)

- **Status**: ✅
- **What it does today**: `form-builder.svelte` manages sections (rename, insert-between, drag-reorder, guarded delete) and fields on a real 12-column grid: drag a field onto another's left/right half to share a row with an equal split, onto empty space for its own row, and drag the divider to redistribute columns (`:76-110, 225-336`). `commit()` flattens back to `(section, sortOrder)`. Save is explicit with a dirty flag, a status line, and a proper `beforeNavigate` cancel-and-confirm guard (`forms/[id]/edit/+page.svelte:30-69`) → `PUT /forms/{uid}/fields`, which replaces the list and re-numbers `sort_order`, preserving `fld_`-prefixed uids (`forms.py:425-468`).
- **Expected**: As an adviser I lay out a fact-find in sections with side-by-side fields and my layout is exactly what the client sees.
  - Order and section persist.
  - Widths persist and drive the rendered grid.
  - Unsaved work is protected.
  - Existing fields keep their identity across saves.
- **Gaps**:
  - **Field width is smuggled into the `validation` bag as `validation.width`** (`form-builder.svelte:68-74`) — a layout hint inside the validation contract, which the field editor must then carefully preserve (`field-editor-sheet.svelte:97`) and which any future validation consumer will trip over.
  - `PUT /fields` is **delete-then-reinsert with no optimistic concurrency** — two staff editing the same form last-write-wins, and any field whose uid isn't `fld_`-prefixed silently gets a new identity.
  - Editing a **published** form's fields is unrestricted and unversioned: clients mid-fill on an open request item get the changed definition (only _templates_ are protected, by their snapshot).
  - No field-level duplicate action, no bulk import from the standard-field library (only a `datalist` hint on the key input).

### Field types

- **Status**: ✅
- **What it does today**: 15 offered types (`packages/ui/src/lib/forms/types.ts:48-64`) all map to a real renderer in `dynamic-field.svelte:38-182` — dedicated inputs for money (with currency code), percentage, date, email, phone, sort code, bank account number, combobox, multi-select, and textarea with min/max counting; legacy aliases (`dateOfBirth`, `multiPicker`, `accountNumber`, `money`) fold onto canonical kinds. Options are authored as label/value rows or advanced JSON with a `{source}` escape hatch (`field-editor-sheet.svelte:192-234`), resolved at runtime by `editor/options.ts`.
- **Expected**: Every type the builder offers renders, validates and formats correctly for the client.
  - No offered type falls through to a plain text box.
  - Choice types cannot be saved without options.
  - Currency/percentage format on display.
- **Gaps**:
  - `{ "source": "…" }` options resolve against a `dataSources` prop that both the preview and the client fill pass as the static `formDataSources` mock — there is no wiring to a **dataset** as an option source, so the advertised escape hatch is effectively decorative.
  - File-upload, signature and address-lookup field types don't exist, though the request/flow layer has separate upload steps.

### Conditions (visibility logic)

- **Status**: ⚠️
- **What it does today**: `conditions-editor.svelte` offers Always / single / group(AND|OR) with a sibling-field picker, 8 operators (`equals`, `notEquals`, `in`, `notIn`, `contains`, `notContains`, `isEmpty`, `isNotEmpty`), best-effort JSON value typing, and a synchronised raw-JSON tab. Persisted as `{conditional: …}` jsonb. At runtime `isFieldVisible` (`packages/ui/src/lib/forms/editor/visibility.ts`) evaluates it with dotted-path and cross-entity (`application.`/`applicant.`/`account.`) resolution, and `DynamicForm` filters to `visibleFields`, validating and submitting **only** those (`dynamic-form.svelte:33-80`) — so a hidden field drops both its error and its answer.
- **Expected**: As an adviser I show a field only when earlier answers warrant it, and the same rule applies in the builder preview and in the client's live form.
  - Builder and runtime evaluate identically.
  - Hidden fields are neither required nor submitted.
  - A condition referencing a deleted field is caught.
  - The server rejects answers that contradict the visibility rules.
- **Gaps**:
  - **Visibility is evaluated client-side only.** The server stores `conditions` as opaque jsonb and never evaluates it (`forms.py:461` just `json.dumps`); `POST /forms/{uid}/responses` and the client path `POST /requests/…/form-response` (`documents.py:1108-1185`) accept **any** answers dict. A crafted request can submit answers to fields that should never have been shown.
  - **Nothing validates that a condition's `field` still exists.** Delete the referenced field and the condition evaluates `getNestedValue → undefined`, so `equals` is false and the dependent field silently disappears from every form forever. Publish does not check this.
  - Unknown operators return `true` (`visibility.ts:default`) — fail-open: a typo in the JSON tab makes a conditional field always visible.
  - The JSON tab writes straight to `value` on every keystroke with no schema validation (`conditions-editor.svelte:102-114`) — any JSON shape persists.

### Validation rules

- **Status**: ⚠️
- **What it does today**: `validation-editor.svelte` authors placeholder, min/max length, regex pattern (with presets), textarea rows, numeric min/max and custom messages, type-gated (`:14-16`), with a JSON tab. `validateField` (`packages/ui/src/lib/forms/editor/validate.ts`) enforces required/empty, string length, pattern (a malformed author regex never blocks the user) and numeric bounds; `DynamicForm` validates on submit and live thereafter.
- **Expected**: As a compliance owner, validation rules I author are enforced wherever the form is filled — including by any API client.
  - Rules enforced client-side with clear messages.
  - **Rules enforced server-side on submission.**
  - Bounds apply to every numeric-ish type.
  - Required means required.
- **Gaps**:
  - **No server-side validation at all.** Both submission endpoints store the answers blob unvalidated — `required`, `pattern`, lengths and bounds are trivially bypassable by any direct API call. This is the single largest correctness gap in the forms area.
  - 💥 **`required` is broken for checkboxes**: `isEmptyValue(false)` returns `false` (`validate.ts:6-12`), so an unticked required consent checkbox passes validation and submits as `false`.
  - 🎭 **min/max bounds are authored for `currency`/`percentage`/`money` but only enforced for `fieldType === 'number'`** (`validation-editor.svelte:16` vs `validate.ts:47`) — a "minimum contribution £1,000" rule does nothing.
  - No cross-field validation (e.g. "end date after start date") and no async/uniqueness checks.

### Publish form

- **Status**: ✅
- **What it does today**: `POST /forms/{uid}/publish` (`forms.py:281-326`) refuses archived forms, requires ≥1 field, is idempotent, and flips `status='published'`. The detail page shows the button only for drafts with `forms:write` (`forms/[id]/+page.svelte:122-148`).
- **Expected**: As an adviser publishing marks the form fit to hand to a client, and validated so it cannot be broken.
  - Empty forms refused; archived forms refused.
  - Idempotent.
  - Publishing validates internal consistency.
- **Gaps**:
  - **Publish is a status flag, not a version.** There are no form versions, so editing a published form changes it in place for everyone, including in-flight fills.
  - Validation is "≥1 field" only: no check for duplicate/blank field keys across the whole set (the sheet checks siblings client-side only), choice fields with zero options, or conditions referencing deleted fields.
  - No unpublish/revert-to-draft (only archive → unarchive, which lands in draft).

### Test submission ("Preview" tab)

- **Status**: ⚠️
- **What it does today**: The Preview tab renders the real `DynamicForm` and submitting posts to `?/submitResponse` → `POST /forms/{uid}/responses` (`forms/[id]/+page.svelte:65-89`, `forms.py:471-510`), which inserts a **real `documents.form_responses` row** attributed to the staff member with `contact_id` null. It then `invalidateAll()`s so it appears in the Responses tab tagged "Standalone".
- **Expected**: As an adviser I test-fill my form to check conditions and validation before it goes to a client, without polluting the real response log.
  - The test exercises the same renderer/validator the client will see.
  - Test data is clearly separated from real submissions (or discarded).
  - Works on drafts.
- **Gaps**:
  - It is **not a test** — it writes a permanent response row indistinguishable from a real one except by the `via` tag, with no way to delete it (no DELETE endpoint for responses). Repeated testing permanently contaminates the submissions the form author reviews.
  - The success copy says "Test submission recorded" (`:258`) — the UI knows it is a test but persists it as production data.
  - No dry-run mode, and no confirmation before persisting.

### Archive / unarchive form

- **Status**: ✅
- **What it does today**: `DELETE /forms/{uid}` soft-archives with a **reference guard** — 409 while any `request_items.form_uid` is still `pending`, with a count in the message (`forms.py:329-372`). `POST /forms/{uid}/unarchive` restores it as a **draft** so it must re-earn publication, 409ing if it isn't archived (`:375-407`). Both are wired from the detail page (confirm dialog) and the list.
- **Expected**: As an ops lead I retire a form without stranding a client mid-fill, and can bring it back.
  - Archive refused while clients are being asked for it.
  - Fields and responses preserved; uid still resolvable.
  - Unarchive returns it as a draft.
- **Gaps**:
  - The guard covers `request_items` only — it does **not** check `flows` step configs (`fill_form.form_uid`) or `templates.input_form_code`, so archiving a form silently breaks a published flow definition or a template's generate dialog.
  - This is the correct pattern the _templates_ domain lacks entirely.

### Form → template linking

- **Status**: ✅ (see also _Link input form_)
- **What it does today**: `templates.input_form_code` → snapshot-at-publish (`templates.py:716-753`) → `GET /templates/{uid}/input-form` reports `snapshot|live|none` → the generate dialog asks those fields → answers land in `documents.inputs` → the worker spreads them into the Jinja context and also exposes `answers` (`worker.py:153-164`). Covered by `test_template_form_snapshot.py`.
- **Expected**: The questions asked at generate time are exactly the published template's frozen form, and the answers fill the document's tokens.
- **Gaps**: The code-not-uid link is rename-fragile (above), and a pre-snapshot published version silently falls back to the _live_ form — a documented behaviour that nonetheless means output can change under a "frozen" version.

### Form → flow linking (`fill_form` steps)

- **Status**: ⚠️
- **What it does today**: `fill_form` is a first-class step type requiring `form_uid` (`app/flows/definitions.py:38-41,93`); flow publish validates the referenced form **is published** (`flows.py:327-341`); the orchestrator creates request-backed items carrying `form_uid` (`flows/step_handlers.py:225-260`), and the client's submission fulfils the item and advances the case (`documents.py:1170`).
- **Expected**: As an adviser I add a "fill this form" step to onboarding and the client's answers advance the case and land where I can see them.
  - Publish refuses unpublished forms.
  - Submission advances the case.
  - Answers are mapped to CRM datapoints where configured.
- **Gaps**:
  - **No UI to author a `fill_form` step's form** — grep finds no form picker in `routes/flows` or the flow components; flow step configs come from the seed (`flows/onboarding_seed.py`) or raw JSON. The linkage is API-complete and UI-absent.
  - Nothing prevents archiving a form that a published flow's step references (above).

### Submissions review

- **Status**: ⚠️
- **What it does today**: `GET /forms/{uid}/responses` (`forms.py:513-545`) keys on `form_uid` so it catches both client request-driven fills and staff standalone rows, tagging `via`. The Responses tab renders each with the submitter, date, and answers formatted through the shared field formatter with real labels (`forms/[id]/+page.svelte:308-340`).
- **Expected**: As an adviser I review what clients actually answered, per client, and export it.
  - Every submission visible with labelled answers.
  - Attributable to a client.
  - Filterable/exportable.
- **Gaps**:
  - **The client is not shown.** `form_responses.contact_id` is populated for request-driven fills (`documents.py:1150`) but the list endpoint doesn't select it — the staff-facing review shows `submitted_by` (the _user_, often null for a client fill) instead of "which client answered".
  - No filtering, search, pagination or CSV export; no per-client roll-up (a form with 500 submissions renders 500 cards).
  - No response detail route, no way to correct or delete a submission.

---

## E. DATASETS

### Create dataset

- **Status**: ⚠️
- **What it does today**: `/datasets/new` offers **Table (inline)** and **SQL** only (`new/+page.svelte:29-32`); the action quote-aware-parses the grid, hard-fails on ragged rows with a precise line-level message, and posts to `POST /datasets` (`new/+page.server.ts`, `datasets.py:386-441`), which validates the code regex, format/liveness/scope enums, exactly-one-source, and a `SELECT` prefix, then inserts the dataset + its **first published version** and caches an `output_schema`.
- **Expected**: As an ops lead I define a data source once, from a table I paste in or a query I write, and templates draw on it.
  - Codes are lower_snake_case and unique.
  - The source matches the format.
  - Column schema is inferred for the editor's field pickers.
  - CSV upload is available.
- **Gaps**:
  - **CSV format is API-only** — `format='csv'` is fully modelled (`csv_file_id`, `rows_from_csv`, `_columns_from_csv`) with **no upload UI**, so a documented format is unreachable.
  - `liveness` (static vs live) is collected and stored but **never consulted by the resolver** (`resolve.py` reads only `format`): a "static" SQL dataset re-executes its query on every render. The axis is decorative.
  - No query validation beyond the `select` prefix: no dry-run/explain, no "does this actually return rows" test at creation.

### Sample data vs live data

- **Status**: ✅
- **What it does today**: `0029_dataset_sample_data` adds a nullable `sample_data` jsonb, author-supplied, **preview-only and never resolved at generate** (`datasets.py:229-254`). Both the create and edit forms offer a sample grid for SQL datasets with clear copy ("Never used at generate", `[id]/+page.svelte:492-507`). `sample_datasets` (`render/sample.py:113-152`) prefers inline rows, then the author's sample, then rows synthesised from column names — never runs live SQL.
- **Expected**: As an adviser my editor preview shows representative data without touching production, and I can supply realistic rows for SQL sources.
  - Sample rows drive the preview only.
  - Inline data doubles as its own sample.
  - Missing samples degrade to synthesis, not blanks.
- **Gaps**:
  - The **chart config preview uses live data** (`GET /datasets/{uid}/data`) while **Preview mode uses sample data** — two different data paths in one editor, with nothing explaining which you are looking at.
  - Sample rows are not validated against the query's inferred columns, so a sample can advertise columns the real query never returns — the preview looks right and the PDF renders blank.

### Version lifecycle

- **Status**: ⚠️
- **What it does today**: Every data edit adds a **new version, immediately `status='published'`** (`_insert_version`, `datasets.py:221-254`), refreshes the cached `output_schema`, and appears in the detail page's history (`[id]/+page.svelte:315-351`). The Sheet copy correctly explains that existing documents keep their snapshot.
- **Expected**: As an ops lead I edit a dataset's data safely, preview the change, then publish — and can roll back.
  - Versions are immutable and listed.
  - Past documents unaffected.
  - Draft-then-publish is possible.
  - Rollback to a prior version is possible.
- **Gaps**:
  - **There is no draft state** — the enum supports `draft|published|archived` but the writer hard-codes `'published'`, so every save goes live immediately. Contrast the templates' proper draft/publish cycle.
  - **No rollback**: no way to re-publish v2 after a bad v3 (you must retype the data). The history list has no actions at all.
  - `_latest_version` prefers published then falls back to the highest draft (`resolve.py:95`), a fallback nothing can currently produce — dead branch that would become a hazard the moment drafts exist.

### SQL execution & security

- **Status**: 💥 (functionally works; security posture is the domain's most serious concern)
- **What it does today**: `_resolve_sql` (`resolve.py:106-150`) rejects anything not starting with `select`, binds `:subject_id` when mentioned, then opens a **SAVEPOINT**, does `SET LOCAL ROLE authenticated`, injects **fabricated JWT claims** (`{"role":"service","permissions":["contacts:read_all"],"tenant_id":…,"tenant_ids":[…]}`), executes the tenant-authored string, and rolls the savepoint back to restore the ambient role/claims. The intent — documented in the docstring and tested (`test_datasets_api.py:516 test_sql_dataset_is_rls_scoped_under_service_role`) — is to keep RLS tenant-scoping in force even inside the `service_role` worker.
- **Expected**: As a platform operator, a firm-authored query can read only that firm's data, cannot mutate anything, cannot escalate, and cannot exhaust the database.
  - Executed read-only, under a least-privilege role, tenant-scoped by RLS.
  - Statement timeout and row cap enforced.
  - Only a trusted, narrowly-granted role may author SQL, and authorship is audited.
  - Author's own data scope is respected at execution time.
- **Gaps** (security):
  - **Fabricated `contacts:read_all` is a privilege escalation.** The injected claims always grant read-all (`:128-139`) and `crm.contacts`'s policy is `(auth.can_read_all() AND tenant_id = ANY(tenant_lineage())) OR …` (`0002_platform_crm.sql:457`). So an adviser restricted to their own book, holding only `datasets:read`, can call `GET /datasets/{uid}/data` on any SQL dataset and read **the whole firm's** contacts/notes/applications. The `_assert_visible_contact` check (`datasets.py:188-201`) only guards the `contact_id` _parameter_, not the query body.
  - **The "SELECT-only" guard is a prefix check on a raw string.** It permits `SELECT … ; UPDATE …` — and when the query contains no `:subject_id`, `params` is empty, which is exactly the psycopg3 path that allows multi-statement execution. Intra-tenant DML through a "dataset" is plausible. It also permits `SELECT … FROM function_that_writes()`.
  - **`set_config` escalation vector.** RLS predicates call `auth.tenant_lineage()`/`auth.can_read_all()`, which read `request.jwt.claims` **at row-scan time**. A query of the shape `SELECT c.* FROM (SELECT set_config('request.jwt.claims','{…other tenants…}',true)) s, crm.contacts c` can plausibly rewrite the claims mid-statement and defeat the tenant scoping the whole design rests on. `set_config('role', …, true)` is likewise reachable. Neither is blocked by the prefix guard. This needs an explicit test to confirm/refute — but the guard offers no defence in principle.
  - **No `statement_timeout`, no `SET TRANSACTION READ ONLY`, no `LIMIT` cap.** A cartesian-product dataset hangs a worker for the full visibility window (5 min), then gets **reclaimed and re-run** — a self-amplifying DoS on the shared render worker.
  - **Authorship is tenant-delegated and unaudited.** `datasets:write` is grantable by any firm admin through `roles:write`, and dataset creation writes no audit row (contrast documents). Effectively, arbitrary SQL against the production database is a firm-configurable permission.
  - Queries are stored and executed as opaque text with no allow-list of schemas/tables, no parse-tree validation, and no separate read-only DB role/replica.
  - Secondary: SQLAlchemy `text()` naively treats any `:word` as a bind parameter, so a query containing `':30'` inside a literal breaks at execution with an opaque error.

### Scope (global / subject / collection)

- **Status**: 🎭
- **What it does today**: `scope` is an enum on the dataset, validated on create/update (`datasets.py:396,456`), shown in the UI and editable in the Details Sheet. At render, the worker takes `subject_id` from the resolved recipient contact (`worker.py:126-137`) and `_resolve_sql` binds it **whenever the query text mentions `subject_id`** — `scope` itself is read nowhere in `render/resolve.py`.
- **Expected**: As an adviser a subject-scoped dataset always resolves for the document's client; a global one is client-independent; and mismatches are caught.
  - Scope drives whether `:subject_id` is required/bound.
  - A subject-scoped dataset with no subject fails loudly.
  - `collection` scope has defined semantics.
- **Gaps**:
  - Scope is **metadata only** — it changes nothing at render. A "global" dataset can bind `:subject_id`; a "subject" dataset that forgets it silently returns firm-wide rows into one client's document.
  - A subject-scoped dataset rendered for a document with no recipient binds `subject_id = None`, so `WHERE client_id = NULL` returns zero rows → silent blank, no error.
  - **`collection` scope is entirely unimplemented** — selectable in the UI, meaningless everywhere else.

### Enable / disable

- **Status**: ✅
- **What it does today**: An `enabled` checkbox in the Details Sheet → `PATCH /datasets/{uid}` (`[id]/+page.server.ts:57-69`). Both resolvers filter on `d.enabled` (`resolve.py:94`, `sample.py:139`), and the link dialog disables un-selectable rows (`dataset-link-dialog.svelte:117-127`).
- **Expected**: As an ops lead I take a dataset out of service without deleting it, and templates using it fail visibly.
- **Gaps**: Disabling resolves to `[]` **silently** in every already-published template that binds it — no warning at disable time about which templates are affected, and no signal at render.

### Delete dataset

- **Status**: ⚠️
- **What it does today**: `DELETE /datasets/{uid}` is a **hard delete cascading to all versions** (`datasets.py:485-499`), fronted by a ConfirmDialog whose copy is "This removes the dataset and its versions" (`[id]/+page.svelte:356-363`).
- **Expected**: As an ops lead I remove a dataset only when nothing depends on it, and never lose the audit trail of documents built from it.
  - In-use datasets are refused (or the deletion is soft).
  - Templates/charts referencing it are listed before deletion.
  - Generated documents' snapshots remain intact.
- **Gaps**:
  - **No reference guard at all.** Deleting a dataset that live, published templates bind by code leaves them referencing a non-existent code, which resolves to `[]` — instantly and silently degrading every future document. The forms domain has exactly this guard (`forms.py:346-362`); datasets have none.
  - Hard delete (not soft) destroys the version history that previously-generated documents' `dataset_snapshots` are meant to be reconcilable against.
  - The confirm dialog does not disclose how many templates use it (the data is trivially available).

### Dataset data preview (detail page + grid)

- **Status**: ⚠️
- **What it does today**: The detail page shows inline rows in a table with a **ragged-row warning** (comma-split corruption detection, `[id]/+page.svelte:53-55,206-215`), or — for `sql`/`csv` — the inferred column pills with "Resolved live at render time". `DatasetDataGrid` provides a real editable grid for authoring.
- **Expected**: As an ops lead I confirm a dataset returns the data I expect before templates depend on it.
  - Inline data is visible and editable.
  - A SQL dataset can be executed on demand against a chosen subject.
  - Errors from the query are shown.
- **Gaps**:
  - **No "Run query" button on the dataset page** — `GET /datasets/{uid}/data` exists and returns live rows, but is only reachable from the template editor's chart-config modal. You cannot test a SQL dataset from the dataset itself.
  - When `_columns_from_sql` cannot parse (any `SELECT *`), the page falls through to "No data preview available for this dataset" with no explanation and no remedy.
  - `loadError` is computed on `datasets/+page.server.ts:23` and never rendered (same silent-empty-list bug as templates).

---

## DOMAIN SUMMARY

| #                             | Function                                   | Status                |
| ----------------------------- | ------------------------------------------ | --------------------- |
| **Templates — list & detail** |                                            |                       |
| 1                             | Create template (wizard)                   | ✅                    |
| 2                             | Templates list (filter/search)             | ⚠️                    |
| 3                             | Edit metadata (Sheet)                      | ⚠️                    |
| 4                             | Publish / version lifecycle / immutability | ⚠️                    |
| 5                             | Link input form                            | ✅                    |
| 6                             | Link datasets                              | ⚠️                    |
| 7                             | Archive template                           | ❌ (endpoint only)    |
| 8                             | Duplicate / delete template                | ❌                    |
| 9                             | Detail preview + version history           | ⚠️                    |
| **Template editor**           |                                            |                       |
| 10                            | Block palette & canvas editing             | ✅                    |
| 11                            | Chart block + data binding                 | ✅                    |
| 12                            | Data table / text table                    | ✅                    |
| 13                            | Variable insertion + live lint             | ✅                    |
| 14                            | Save / flush pipeline                      | ⚠️                    |
| 15                            | Version preview / diff / restore           | ⚠️                    |
| 16                            | Import / export JSON                       | ⚠️                    |
| 17                            | Page setup (per-page geometry)             | ✅                    |
| 18                            | Header / footer                            | ⚠️                    |
| 19                            | CSS mode                                   | ✅                    |
| 20                            | Preview mode / WeasyPrint parity           | ⚠️                    |
| 21                            | Undo / redo + history menu                 | ✅                    |
| 22                            | Theme panel                                | 🎭 (`{#if false}`)    |
| 23                            | Editor datasets panel                      | 🎭 (`class="hidden"`) |
| 24                            | localStorage draft recovery                | 🎭 (no `storageKey`)  |
| **Render pipeline**           |                                            |                       |
| 25                            | `POST /documents/generate` + gate          | ✅                    |
| 26                            | DocumentWorker render → storage            | ✅                    |
| 27                            | Retry / dead-letter                        | ⚠️                    |
| 28                            | Failure surfacing to the user              | 💥                    |
| 29                            | Dataset resolution at render               | ⚠️                    |
| 30                            | Renderer selection (Weasy/Chromium)        | ⚠️                    |
| **Forms**                     |                                            |                       |
| 31                            | Create form                                | ✅                    |
| 32                            | Form builder (sections/order/widths)       | ✅                    |
| 33                            | Field types                                | ✅                    |
| 34                            | Conditions (visibility)                    | ⚠️                    |
| 35                            | Validation rules                           | ⚠️                    |
| 36                            | Publish form                               | ✅                    |
| 37                            | Test submission                            | ⚠️                    |
| 38                            | Archive / unarchive                        | ✅                    |
| 39                            | Form → template linking                    | ✅                    |
| 40                            | Form → flow linking (`fill_form`)          | ⚠️ (no UI)            |
| 41                            | Submissions review                         | ⚠️                    |
| **Datasets**                  |                                            |                       |
| 42                            | Create dataset (inline / SQL)              | ⚠️                    |
| 43                            | Sample vs live data                        | ✅                    |
| 44                            | Version lifecycle                          | ⚠️                    |
| 45                            | SQL execution & security                   | 💥                    |
| 46                            | Scope (subject/global/collection)          | 🎭                    |
| 47                            | Enable / disable                           | ✅                    |
| 48                            | Delete dataset                             | ⚠️                    |
| 49                            | Dataset data preview                       | ⚠️                    |

Tally: ✅ 17 · ⚠️ 22 · 🎭 4 · ❌ 2 · 💥 2

---

## TOP 5 SEVEREST GAPS

1. **Tenant-authored SQL is executed with fabricated read-all claims, a prefix-only "SELECT" guard, and no timeout** (`apps/api/app/render/resolve.py:106-150`). The injected claims grant `contacts:read_all` unconditionally, so any holder of `datasets:read` can exfiltrate the firm's entire client book through `GET /datasets/{uid}/data`, bypassing their own book scoping. The `startswith("select")` guard does not prevent multi-statement DML (empty-params path), function side effects, or `set_config('request.jwt.claims'|'role', …, true)` mid-query — which targets the very GUC that every RLS predicate reads at scan time. No `statement_timeout`, no `SET TRANSACTION READ ONLY`, no row cap, and a runaway query is _reclaimed and re-run_ after the 5-minute visibility window. Fix: parse-validated single-statement SELECT, a dedicated read-only role/replica, real caller claims (no fabricated `read_all`), `statement_timeout` + `LIMIT`, and an audit row per dataset authored.

2. **Render failures are diagnosed and then thrown away.** The worker writes a precise error into `documents.documents.error` and `render_jobs.last_error` (`apps/api/app/worker.py:263-271`), but `DocumentOut` has no `error` field (`apps/api/app/routers/documents.py:75-108`) and the status probe returns `{status}` only — so every failure reads as `'Rendering failed. Check the template and try again.'` (`apps/platform/src/routes/documents/create/+page.svelte:38`). Compounding it: **no retry/regenerate action anywhere** and **no dead-letter view**, so a failed document is terminal and its cause is only in worker stdout.

3. **A missing, deleted, disabled or mis-typed dataset silently produces a blank client document.** `resolve_datasets` swallows every failure into `[]` (`resolve.py:159-181`); the lint adds every referenced code to the "known" set (`templates.py:437`) so the generate gate cannot see it; `DELETE /datasets/{uid}` is a hard cascade with **no in-use guard** (`datasets.py:485-499`). A firm can delete a dataset and keep producing plausible-looking, empty-tabled suitability reports with no signal at any layer. (Contrast `forms.py:346-362`, which does this correctly.)

4. **Form conditions and validation are client-side only, and `required` is broken for checkboxes.** Both submission endpoints (`forms.py:471-510`, `documents.py:1108-1185`) persist the answers blob without evaluating `conditions` or `validation`, so required fields, regex patterns, lengths and bounds are bypassable by any direct API call. Client-side, `isEmptyValue(false) === false` (`packages/ui/src/lib/forms/editor/validate.ts:6-12`) means an unticked **required consent checkbox submits successfully**, and authored min/max bounds on `currency`/`percentage` fields are never enforced (`validate.ts:47` gates on `fieldType === 'number'`).

5. **Published-version immutability is promised but not enforced, and the template lifecycle has holes at both ends.** `PATCH /templates/{uid}` writes `renderer` to the latest version regardless of status (`templates.py:296-305`), mutating a published version the UI calls immutable (`templates/[id]/+page.svelte:545`); there is no DB trigger backing the `0012_templates.sql:43` comment. At the same time **archive has no UI and no unarchive endpoint**, the "Archived" filter can never match (list excludes archived), **an archived template can still be generated from** (`documents.py:352-367` has no `archived_at` filter), and duplicate/delete do not exist at all. Separately, the editor has **no autosave, no dirty guard and a fire-and-forget `beforeNavigate` flush** (`templates/[id]/edit/+page.svelte:44-47`) plus a dead localStorage cache — so unsaved editor work is genuinely losable.
