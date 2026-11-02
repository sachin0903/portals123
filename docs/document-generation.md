# Portal — Document Generation & Template System

Status: core spec for building the template and generation system into the portals monorepo, which is greenfield.

> **Landed so far (authoring half).** Migration `0012_templates` ships `documents.templates` + `template_versions` (+ the `datasets`/`dataset_versions` tables, enums, RLS, the deferred templates↔versions FK, and the `documents.documents.template_version_id` FK) — before `0002`, as §9 anticipated. The `/templates` router (`apps/api/app/routers/templates.py`, `tenant_session`, gated by `templates:read`/`templates:write`) implements CRUD, version list/get, **save-draft** (upserts the latest draft in place so the editor's autosave doesn't spawn a version per keystroke; starts a fresh `v(n+1)` once the latest is published), and **publish** (validates the version has content — the Jinja syntax + undeclared-variable checks land with the renderer). The platform **list, detail and GrapesJS editor** are wired to it (`$lib/templates.ts` mapper; the editor loads the current version's blob via a `restore` prop and autosaves via an `onSave` callback → the save-version action). The editor's **History panel reflects the real `template_versions`** (passed as `versions`; an older version's blob is fetched on demand via `GET /templates/{uid}/versions/{vid}` behind an `onLoadVersion` callback) — not a per-session localStorage log; preview/restore load a real version and the offline localStorage cache remains only for draft recovery. Tests: `tests/test_templates_rls.py`, `tests/test_templates_api.py`. **Portal-shape divergences (deliberate):** the version `schema` holds the editor's `TemplateContent` (not the prototype's GrapesJS `{html,css,components}`), and `documents.templates` carries `linked_datasets` (dataset codes) + `input_form_code` (a real `documents.forms` code — the §9.1 link, now persisted) at the template level. The **datasets catalogue** is now real too: the read-only `/datasets` router (`apps/api/app/routers/datasets.py`, `tenant_session`, `datasets:read`) lists the firm's datasets and, for `static` datasets, returns the sample rows the detail page previews (from the published version's `static_data`). The platform **datasets list + detail** and the templates **link dialog / create-wizard dataset step** are wired to it (`$lib/datasets.ts` mapper); dev data comes from `scripts/seed_datasets.py`. Tests: `tests/test_datasets_rls.py`, `tests/test_datasets_api.py`. The **generate dialog** (`/documents/create`) is now fed real inputs too: its loader resolves the template, its input form's fields via **`GET /templates/{uid}/input-form`**, and the tenant's real clients, and the dialog collects + reviews them through `DynamicForm`. **The input form is frozen at publish (`0019_template_form_snapshot`):** publishing snapshots the linked form's fields into `template_versions.input_form_snapshot`, and generation reads that snapshot — so editing the form later never changes an already-published template; you republish to pick up form changes (the endpoint falls back to the live form by code for versions published before snapshots existed). Tests: `tests/test_template_form_snapshot.py`. — the final render is an honest stub (an "inputs captured, pipeline lands next" step) until the pipeline below exists. The editor's `{{ token }}` autocomplete + jinja-var picker draw from the same linked form's fields (resolved by the edit page loader from `input_form_code`, passed to `TemplateEditor` as `formFields`) — the old per-doc-type mock generation form and its `generate-form.ts` adapter are gone. **Document generation now renders for real (async queue + worker, §8/§10).** The editor persists a render-ready export (Jinja-tokenised `html` + `css` + `pageSize`) into the saved version blob via `buildSchema` (T4a). `POST /documents/generate` (`apps/api/app/routers/documents.py`, `documents:write`) **enqueues**: it inserts a `queued` document — recording the `inputs`, the template version and (via `document_contacts`) the client it's for — plus a `documents.render_jobs` row, in one transaction, and returns immediately. The **DocumentWorker** (`app/worker.py`) drains the queue (`SELECT … FOR UPDATE SKIP LOCKED` — a table-queue standing in for PGMQ, so no PG18 extension + it keeps the existing DB/data; swappable behind the same interface), running as `service_role` and stamping `tenant_id` explicitly (§10). Per job it loads the version, builds the Jinja2 context (inputs + resolved client), renders through `app/render/` (sandboxed `jinja_engine` + a `DocumentRenderer` protocol — **WeasyPrint** default, **Playwright/Chromium** opt-in, both ported from platform-1), stores the PDF via `storage.rendered_key`, sets the doc `generated` (with `pdf_file_id`/`renderer_version`/`generated_at`), writes the `document.generated` audit event, and acks; on error it retries with backoff up to `max_attempts`, then dead-letters (job + doc `failed`). The generate dialog enqueues then **polls** `GET /documents/{uid}` status (§8.1) and navigates on `generated`. The list projection + retrieval fall back to `pdf_file_id` so generated docs (no `original_file_id`) are listable + viewable. Tests: `tests/test_generate_api.py` (enqueue + worker claim/render/retry, renderer + storage mocked). **Where the worker runs:** WeasyPrint's native stack (Pango/Cairo/GLib) installs cleanly on Linux but not Windows, so in dev the worker is a **Docker service** (`apps/api/Dockerfile.worker` + the `render` service in `docker-compose.local.yaml`, `pnpm worker:up`) that reaches the host Postgres by container name on a stable external network (`graphene-dev`); the Windows-native API just enqueues. Prod runs the same image alongside the API. **Landed since:** **render-time dataset resolution + chart/table rendering** — the worker resolves each linked dataset to rows (`app/render/resolve.py`: `inline` stored rows, `catalogue` entries compiled and run live), merges them into the Jinja context, and substitutes the editor's `<g-chart>`/`<g-table>` markers with server-rendered visuals (`app/render/{charts,tables,components}.py` — charts → pure-Python inline **SVG** since WeasyPrint runs no JS, tables → styled HTML), snapshotting the resolved rows into `documents.dataset_snapshots`. And **dataset authoring** — the `/datasets` router now does create/update/delete/version + a resolved-data (`GET /datasets/{uid}/data`) endpoint (`datasets:write`), on the **catalogue × inline** format model (`0116_dataset_catalogue`; free-text SQL and the unreachable server-side CSV dropped as sources, and `liveness` with them — it was derived from the format, never chosen). **Editor tables & charts.** The Tables palette has **two** blocks: a **Text table** (`doc-texttable`) — a static grid the author types into directly (real `data-gjs-type=text` cells; add/remove rows & columns from the right panel), which serialises to a plain `<table class="doc-jtable-inner">` and so needs **no** render-pipeline handling — and a **Data table** (`doc-jtable`) — the dataset-driven `{% for %}` loop. Both share the `JDesign` styling (`--jt-*` vars). Charts render inside a default white **card** (hairline border + rounded corners) with softened grid/axis colours. Two parity constraints: table styling lives in **both** `DOC_JTABLE_CSS` (`editor/component-types.ts`, shipped in the export) and `static/grapesjs-canvas.css` (the canvas copy); chart styling lives in **both** `editor/chart-preview.ts` (editor SVG) and `app/render/charts.py` (PDF SVG) — change the pair together or editor and PDF drift.

**Dataset authoring UX.** Inline data is edited through a shared **editable grid** (`components/datasets/dataset-data-grid.svelte` — add/remove rows & columns, per-cell inputs, **Import CSV** via `$lib/csv.ts` `parseCsvFile`) rather than a comma-separated textarea. Both the **New dataset** form (`routes/datasets/new`) and the **dataset detail** page use it; the grid serialises back to the `columns`/`rows` string contract (quote-aware, via `toCsvLine`). **Datasets are editable + versioned in the UI:** the detail page edits metadata (`PATCH /datasets/{uid}` — name/description/scope/enabled; format stays immutable) and edits data by publishing a **new version** (`POST /datasets/{uid}/versions` — the grid for inline, the settings form for a provided entry), with a **version-history** panel fed by `GET /datasets/{uid}/versions` (newest-first; carries each version's source so the editor seeds from the current one). A provided entry's settings render through `components/datasets/dataset-params.svelte`, where a date offers the rolling periods as a menu with "a fixed date" as one entry — a date picker alone could not express `-12m`, which is the only form that stays current. Because generation snapshots resolved rows into `dataset_snapshots`, editing never retroactively changes an already-generated document — future renders pick up the new version.

**Still deferred:** literal PGMQ + a DLQ table (the interface is ready), disclosure/calc resolution, the `preview`/`diff` endpoints, and **file-backed** `csv` datasets (format `csv` + `csv_file_id` — a stored CSV in object storage; the model + API accept it, but the create UI imports CSVs into an inline dataset instead of uploading the file).

Scope: the template system (authoring, versioning, publishing) and document generation (the async render pipeline that turns a template plus inputs into a stored PDF). Listing, viewing, sharing, tasks and archiving the result belong to document management, a separate system this one hands off to.

A working reference implementation already exists in the platform repo (`apps/platform` on Next.js, `services/api` on Python). The portal is a different stack: SvelteKit, a single FastAPI, and Postgres with row level security, and it has only auth today. This spec describes what to build in the portal and uses the prototype as the proven design. Section 2 says what can be lifted and what has to be rebuilt.

---

## Scope and boundaries

In scope: template authoring (the GrapesJS editor, variable schema, dataset linking, fixtures, typed components, disclosures); the template lifecycle (draft to publish, validation, versioning, preview, diff); datasets (registry, sources, scopes, versioning, render-time resolution); calculations; and document generation (the generate flow, the trigger API, the queue and worker, the render pipeline, and the output record plus files plus event).

Adjacent systems, covered elsewhere (see section 13): document management, sharing, tasks and tags, the CRM/clients model, notifications, object storage, theming, and auth. Generation's job ends once it has written the document record, the files, and a `document.generated` event.

---

## 0. Foundation reconciliation (portal `0001`)

An earlier draft assumed a generic `tenant_id uuid` + `current_setting('app.current_org_id')` RLS model with an `app_role`. **That is not the portal's shipped `0001_auth_core` foundation.** This spec is reconciled to it; the conventions below apply throughout (DDL §17 follows them) and override any residual mention of the old model. They are identical to the Document Management spec's §0 (the two share the `documents.*` schema):

- **`int` tenant key + `int` PK / `text uid` convention.** `tenant_id int NOT NULL REFERENCES auth.tenants(id)`; internal `id int` + public `uid text UNIQUE`; person refs `uuid REFERENCES auth.users(id)`.
- **Claim-driven RLS, not `app.current_org_id`.** `tenant_session` does `SET LOCAL ROLE authenticated` + publishes `request.jwt.claims`; policies use `auth.tenant_lineage()` / `auth.uid()` / `auth.is_system()` / `auth.can_read_all()` / `auth.contact_scope()`.
- **Roles:** `authenticated` (request, grants `TO authenticated`), `service_role` (BYPASSRLS — the **worker** and `SECURITY DEFINER` owners), `migrator` (DDL; a member of `service_role`). No `app_role`/`migration_role`.
- **Worker RLS context (replaces `set_config('app.current_org_id', …)`).** A worker has no HTTP `Principal`. Follow the `0001` system pattern: open a transaction as `service_role` (BYPASSRLS) and stamp the job's `tenant_id` explicitly on every write, **or** mint a minimal `request.jwt.claims` for the job and `SET LOCAL ROLE authenticated`. Do **not** invent an `app.current_org_id` GUC — it isn't part of this foundation. This is the same strategy the share endpoint uses (DEFINER/`service_role`); agree it once across both specs.
- **`subject_ref` is `int`** (`subject_id int`, FK targets `crm.applications`/`platform.accounts`/`crm.contacts` land in `0002`), shared identically with the Document Management migration.

---

## 1. Current portal state

The portal (`portals/`) has the **`0001_auth_core` foundation** built (not just bare auth):

- `apps/api` (FastAPI) has `health`, `me` and `admin` routers. The first migration `0001_auth_core` is in (`auth`/`audit` schemas, the claim-driven RLS helpers + `auth.resolve_claims`, the append-only `audit.log` + DML trigger). `tenant_session` (`app/db.py`) is **fully wired and in use** (e.g. `/me/tenant`) — it sets `SET LOCAL ROLE authenticated` + `request.jwt.claims`, **not** `app.current_org_id`. No _domain_ schema beyond `0001` yet.
- `apps/platform`, `apps/client`, `apps/admin` (SvelteKit) have login, MFA, verify, settings — plus the document-management UI built against mock data (`$lib/demo.ts`), pending these backends.
- Python deps present: FastAPI, SQLAlchemy 2 (async), Alembic, psycopg, WorkOS, PyJWT. Not present: any queue (PGMQ), Jinja2, WeasyPrint/Playwright, a chart library, or an S3/MinIO client.
- No worker/scheduler process exists yet.

So what this system adds is: the template/dataset half of the `documents.*` schema (it FKs only `auth.*`, so it can land before `0002`), the queue, the worker, the Python render pipeline, the storage integration, the FastAPI endpoints, and the SvelteKit editor + generate UI. The _generated-document_ tables bind to clients/accounts and so wait on `0002` (section 9).

---

## 2. What can be lifted, what gets rebuilt

| Concern          | Prototype                    | Portal                  |
| ---------------- | ---------------------------- | ----------------------- |
| Render pipeline  | Python in `services/api`     | Lift                    |
| Data model       | Drizzle, integer ids, no RLS | Rebuild (Alembic + RLS) |
| Worker and queue | PGMQ + `DocumentWorker`      | Build                   |
| Template editor  | React GrapesJS (~7.5k LOC)   | Rebuild (Svelte)        |
| Generate modal   | React (~780 LOC)             | Rebuild (Svelte)        |
| API              | Next.js route handlers       | Rebuild (FastAPI)       |
| Charts           | Next.js `/api/charts/render` | Rebuild (Python SVG)    |

Notes:

- Render pipeline is the main reuse. The Jinja sandbox, calc library, chart/table/disclosure/projection substitution, disclosure registry and WeasyPrint renderer are all Python, so they move into `apps/api` once storage and DB access point at the portal's clients.
- Data model: rebuilt as Alembic migrations with **int** ids (`id int` + public `uid text`), an **`int tenant_id`** FK to `auth.tenants`, and the RLS policy in the same migration (section 9). RLS is the `0001` claim model (`tenant_id = ANY (auth.tenant_lineage())`), **not** `app.current_org_id`. Shares the `documents.*` tables with the Document Management spec.
- Worker and queue: provision PGMQ and handle the worker's tenant context under RLS (section 10).
- Template editor: GrapesJS is framework-agnostic (vanilla JS), so it embeds in a Svelte component; the React wrapper does not carry over.
- Charts: there is no Next.js in the portal, so render charts as pure-Python SVG in the worker (section 5.1).
- API: surfaced through the generated `@graphene/api-client` (section 8).

---

## 3. The three layers

1. Templates: versioned definitions of layout and required inputs (a GrapesJS document of HTML/CSS plus component metadata, a variable schema, and linked datasets). Published versions are immutable.
2. Documents: instances rendered from a template version, an input payload, and resolved dataset snapshots. Scoped by `tenant_id` under the `0001` claim-driven RLS (`auth.tenant_lineage()`). Generation's job ends once this record is written; management takes it from there.
3. Files: the PDF (and an HTML snapshot) in per-tenant object storage under a **path-addressed** key (`documents/{tenantId}/{documentUid}/v{n}.{pdf|html}`, per the Document Management spec, which owns `documents.files`), referenced from the document record. `sha256` is recorded for integrity, not used for keys; v1 does not deduplicate (and couldn't — byte-identical output is a non-goal, section 11).

---

## 4. Target stack

| Layer           | Technology                                                                                                                                                                            |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| UI              | SvelteKit 2 / Svelte 5 (runes) / Tailwind 4 / shadcn-svelte, with shared `@graphene/ui`. The editor and generate UI live in `apps/platform`; clients view documents in `apps/client`. |
| Template editor | GrapesJS embedded in a Svelte component, plus a typed component registry                                                                                                              |
| API             | One FastAPI (`apps/api`) on async SQLAlchemy 2 and Pydantic v2. OpenAPI generates `@graphene/api-client` (Hey API).                                                                   |
| Worker          | A background consumer (`DocumentWorker`) hosted alongside the API. New; the repo has no worker yet.                                                                                   |
| Database        | Postgres 18 with `tenant_id` and RLS, Alembic migrations, and two DB roles (app for DML under RLS, an elevated role for migrations)                                                   |
| Queue           | PGMQ (to provision): `documents_render` plus a dead-letter queue                                                                                                                      |
| Render          | Jinja2 sandbox plus WeasyPrint (default) or Playwright (opt-in) behind a `DocumentRenderer` protocol                                                                                  |
| Charts          | Server-side SVG, rendered in Python in the worker (section 5.1)                                                                                                                       |
| Storage         | Per-tenant S3-compatible bucket with short-lived signed URLs                                                                                                                          |
| Auth            | WorkOS (JWT verified in FastAPI, sealed session in SvelteKit); the app owns authorisation (RBAC, permissions, RLS)                                                                    |

---

## 5. Template authoring — GrapesJS and components

The editor is GrapesJS embedded in a Svelte component in `apps/platform`. Free-form HTML/CSS is constrained by a typed component registry so regulatory artefacts stay well-formed. Each component is registered with GrapesJS on the editor side (TypeScript) and has a matching server renderer (Python). They share a contract:

```
props_schema  : Zod (TS, editor) <-> Pydantic (FastAPI, worker)
data_binding  : Jinja expression resolved at render time (binding._resolved_data)
editor_render : live Svelte/GrapesJS component with sample data
server_render : Python (props, resolved_data) -> inline HTML/SVG fragment
```

Components appear in the template HTML as markers keyed by `data-id` into `schema.components[]`, and are substituted server-side at render time:

| Marker                     | Types                                                                       | Server output                            |
| -------------------------- | --------------------------------------------------------------------------- | ---------------------------------------- |
| `<g-chart data-id>`        | `line_chart`, `bar_chart`, `pie_chart`, `donut_chart`, `stacked_area_chart` | Inline SVG (tenant colours)              |
| `<g-table data-id>`        | table                                                                       | Styled HTML table                        |
| `<g-disclosure data-code>` | regulatory disclosure                                                       | Registry text (section 5.4)              |
| `<g-projection-table>`     | projection table                                                            | HTML table from `scenario_projections()` |

Scalar text, headings, key-value blocks, `repeat`/`conditional`, and page breaks are plain Jinja/HTML. Adding a component means a new entry in both registries (plus a substitution branch for markers); the DB schema does not change.

### 5.1 Charts

The prototype renders chart SVG by calling a Next.js endpoint. The portal has no Next.js and the worker is Python, so charts are rendered as pure-Python SVG in the worker (matplotlib or altair, producing deterministic, seeded SVG).

One renderer drives both the PDF and the editor preview: the editor previews the same worker-rendered SVG through a preview endpoint, not a separate JS chart library. A second JS renderer "for interactivity" is rejected on purpose. For regulated illustrations the adviser has to approve exactly what the client receives, and two renderers would guarantee a mismatch. Keeping one renderer also avoids a frontend round-trip during generation.

Manual (inline) data: a chart or table need not bind a dataset. The config offers a **Dataset | Manual** toggle; Manual stores author-entered (or CSV-imported) rows on the component itself (`binding._inline_rows`, a `{columns, rows}` blob), and the worker prefers those rows over any dataset binding (`app/render/components.py`). This covers one-off tables/charts that don't warrant a first-class dataset — the "upload a CSV → static table" case — without a resolution or RLS path (the rows are literal, author-typed).

### 5.2 Persistence (`template_versions.schema`, JSONB)

```jsonc
{
  "html": "...{{ client.full_name }}...<g-chart data-id=\"comp_1\"/>",
  "css": ".cover { ... }",
  "components": [
    { "id": "comp_1", "type": "line_chart",
      "props": { "title": "Projected value", "y_label": "GBP" },
      "binding": { "_resolved_data": "{{ projection_rows }}", "x_field": "year", "y_field": "value" } }
  ],
  "datasets": ["ds_projection_default"],
  "calculations": ["compound_growth"],
  "variable_schema": { "variables": [...], "loops": [...] },
  "gjs_project": { ... },
  "pageSize": { "width": "210mm", "height": "297mm" }
}
```

On publish, two artefacts are derived and stored: `input_schema` (a JSON Schema built from `variable_schema`, which drives the generate form) and `fixtures` (sample values from `.sample`, used as the preview context and the generate-form defaults).

### 5.3 Draft to publish lifecycle

States run `draft -> published -> archived`, with a monotonic `version_number`, a `templates.current_published_version_id` pointer, and one published version active at a time. Publish validation:

1. The version is a draft belonging to this org.
2. `html` is non-empty.
3. A Jinja2 syntax check runs through the render pipeline; a render error rejects the publish.
4. A variable scan checks that every `{{ var }}` token is declared in `input_schema` (Jinja built-ins excluded); undeclared names return a 400 listing them.
5. On success, set the version `published`, archive the prior published version, and update the pointer.

A version diff supports side-by-side comparison.

### 5.4 Disclosure registry

A shared set of FCA disclosures keyed by code (`cobs13_general`, `cobs13_risk`, `cobs13_charges`, `consumer_duty`, `sysc9_retention`, `ifa_not_advice`, `sipp_risk`, `projection_basis`), referenced as `<g-disclosure data-code="..."/>`. The lookup supports tenant overrides.

---

## 6. Datasets

Datasets are first-class entities scoped by `tenant_id`. Templates reference them by **code**; the worker resolves them at render time and snapshots the result onto the document.

A dataset has one of two **formats** (migration `0116_dataset_catalogue`):

- **`catalogue`** — a query **Graphene** wrote, which a firm picks and configures. Entries live in code (`app/render/catalogue.py`), not in rows, so every firm has them the moment they ship and none of them can drift per tenant. A key is a **bare identifier** (`book_valuations`) because a template binds a dataset by writing `{% for row in <code> %}` and `components.loop_sources` drops a DOTTED source — a namespaced key (`book.valuations`) was silently never resolved, and the table rendered blank. The `book_` prefix is reserved: a firm's own code may not start with it, or it would shadow the shipped entry. `GET /datasets/catalogue` describes each entry and what it lets a firm set; `GET /datasets` lists them alongside the firm's own rows.
- **`inline`** — a table of rows the firm typed in (or imported from a CSV in the browser), stored as a snapshot.

**Free-text SQL authoring is gone.** A firm could type any `SELECT`, and the whole defence was a keyword denylist, a function denylist, a schema-prefix denylist, a hand-written lexer and a bind allowlist — around 370 lines guarding a query no reviewer had seen. What a firm actually needed was never "any query": it was "the valuations table, for this client, over this period". So the query is ours and the choices are theirs. The unreachable server-side `csv` format went at the same time — no upload endpoint ever existed for it, and the working CSV import is client-side and lands as `inline`.

The replacement is a smaller question, not a better validator: **a setting is a value bound by the driver, never text spliced into SQL.** No setting names a table, a column, an ordering or a predicate, so an entry's SQL is a constant. `Entry.__post_init__` refuses a declaration that could not be safe — duplicate column names, a parameter shadowing a resolver bind, `:bind::cast` shorthand (SQLAlchemy reads `:from_date::date` as a bind named `from_dat`), a per-client entry that does not bind `:subject_id`, and a firm-wide one that does.

**Settings, and why a date has two modes.** A parameter is declared with a kind (`date` / `int` / `bool` / `enum`), a label and a default. A date may be an ISO date, `today`, or a **relative token** (`-12m`), and the token is what gets stored — resolved on every render. That is the whole point: a quarterly statement configured once with a fixed date would report the same fixed quarter for ever. The authoring UI offers the rolling periods as a menu with "a fixed date" as one entry, so the two modes are visible rather than one of them being unreachable.

**Scope** is `subject` (about one client) or `global` (the same rows for every client). It is not a label — a `global` dataset is resolved **firm-wide** at render (`_minted_claims` mints `contacts:read_all` for the dataset's own tenant), which is why configuring one takes `contacts:read_all` as well as `datasets:write`: the entitlement to declare firm-wide data is the entitlement to see it. Only a catalogue entry can be per-client, because only its SQL binds the render's client, and the **entry's** declared scope wins over anything a stored row says.

**A per-client entry scopes itself, and RLS is the backstop — not the mechanism.** This is measured, not assumed. `platform.valuations` has no `contact_id` column, so its read policy's only client-side branch is `account_id = ANY (auth.account_scope())`; with the accounts minted empty, a per-client valuations query returned **zero rows** — in a client's statement, indistinguishable from a client who holds nothing. `platform.contributions`' read policy, meanwhile, is `tenant_lineage OR account_scope` with **no** `auth.can_read_all()` gate, so the same claims made the **whole firm's** contributions visible. The two fail in opposite directions, so the policy set cannot be the scoping rule: every per-client entry filters on `:subject_id` in its own `WHERE`, minted subject claims now carry that client's own accounts, and `tests/test_dataset_catalogue_execution.py` proves both halves per entry against real Postgres.

**The runtime envelope is what survived the validator**, and it is now the whole of the defence. Every dataset query — ours included — goes through one function (`resolve.py::_execute_scoped`): `SET LOCAL ROLE authenticated` so RLS applies inside a `BYPASSRLS` session, minted claims matching the declared scope, `transaction_read_only`, a statement timeout, a database-enforced row cap, a `SAVEPOINT` rolled back afterwards, and a **post-statement principal re-check** that discards the rows if the statement moved the role or `request.jwt.claims` while it ran. That last layer matters most now: it depends on nothing having named every spelling of `set_config`, so a rewrite reached through a view's function or a custom operator is caught there or nowhere.

**No dataset reads the lake.** `book_holdings` is deliberately absent: holdings are lake-only, SEI publishes none of the columns `app/lake/holdings.py` reads (zero of eight), Morgan Lloyd has no positions model at all, and the render container holds no lake credential. Beyond that, `lake_reader` reaches the whole warehouse with no row scoping and none of the envelope above exists on a DuckDB connection — so a lake-backed entry could not claim to inherit any of it. `tests/test_dataset_render_isolation.py` also refuses any render module that imports `app/lake/fixture.py` or the display seam it degrades through: the fixture serves a committed extract when the warehouse is unreadable, which is right for a portal screen and would be a **fabricated valuation** in a client's statement.

**Entitlement** is checked when a firm configures or links an entry, and on the live data door — never at render. A queued render has no acting human (the worker drains `render_jobs` with a tenant id and no principal), so the only boundary it can enforce is the envelope's.

**Lifecycle.** Datasets are versioned independently of templates. A template pins codes, not versions, so the latest published version resolves at render time. The resolved payload is snapshotted into `dataset_snapshots`, so regeneration reads the snapshot rather than live data — that reproducibility is a regulatory requirement, and it also means editing a dataset never retroactively changes an already-generated document.

**Preview sample data.** A dataset version may carry an optional `sample_data` blob (`{columns, rows}`, migration `0029_dataset_sample_data`) — author-entered rows used **only** by the template editor's live preview, never at generation. A catalogue entry needs none: `render/sample.py` synthesises rows from the entry's **declared** columns, which is also what the merge-field picker reads. `inline` datasets need no sample either — their rows serve as both the data and the preview.

---

## 7. Calculations

A controlled Python library, liftable from the prototype, is exposed to templates as sandboxed Jinja globals. All arithmetic uses `Decimal`.

| Function                  | Purpose                                         |
| ------------------------- | ----------------------------------------------- |
| `compound_growth`         | Forward projection with optional contributions  |
| `scenario_projections`    | Cautious/intermediate/optimistic, net of charge |
| `fee_impact`              | Gross vs net-of-charges, with fee drag          |
| `drawdown`                | Decumulation under withdrawals                  |
| `allocation_breakdown`    | Percentage allocation from holdings             |
| `performance_attribution` | Weighted return vs benchmark                    |
| `scenario_comparison`     | Multiple scenarios side by side                 |

Documents record `calc_lib_version`. The library computes; the renderer only formats.

---

## 8. Generation flow and API

Endpoints live in the single `apps/api` and reach the SvelteKit portals through the generated `@graphene/api-client`. Operation ids follow `{tag}-{route_name}`, so keep `name` and `tags` stable. Every tenant-scoped route uses `tenant_session` (RLS) and an authorisation check (section 12).

Templates: `GET/POST /templates`; `GET/PATCH/DELETE /templates/{uid}`; `GET /templates/{uid}/versions`; `POST /templates/{uid}/versions` to save a draft (the prototype lacked this route, so build it here); `GET /templates/{uid}/versions/{vid}`; `GET /templates/{uid}/versions/{vid}/diff/{other}`; `POST /templates/{uid}/versions/{vid}/preview`; `POST /templates/{uid}/versions/{vid}/publish`.

Datasets: `GET/POST /datasets`; `GET/DELETE /datasets/{uid}`; `GET /datasets/{uid}/data` (a capped row preview); `POST /datasets/{uid}/versions` (CSV/SQL/static); `POST /datasets/{uid}/versions/{vid}/publish`.

Generation:

| Method | Path               | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------ | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| POST   | `/documents`       | Body `{ template_uid, inputs, dataset_uids?, subject_ref? }` (`subject_ref` is the `(subject_type, subject_id)` pair). Resolve the published version, merge template-pinned and explicit datasets, insert a `documents` row (`tenant_id` from the principal, and the other shared insert-time columns: `source='generated'`, `document_type`, `name`, `subject_ref`, `status='queued'`), and enqueue to PGMQ, all in one transaction. Returns the record (`uid`, `status: 'queued'`). |
| GET    | `/documents/{uid}` | Poll generation status.                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

**Batch generation — one template, many clients (`POST /documents/generate/batch`).** An adviser picks a template and a set of clients and gets one document EACH: its own `documents.documents` row, its own render job, its own `'subject'` link — the choice `messaging_groups.create_group_request` already makes for a group request, because each document is filed, sent, superseded and audited on its own. `answers` and `name` are shared; everything that differs per client comes from the render context (the client, their adviser, their accounts), which is what makes this worth having rather than a loop the caller could write.

- **Partial success is the contract.** A client the caller may not target is named in `rejected` and the rest still queue. Nothing landing at all is a 422 naming every reason, so an empty `documents` can never read as success. Conditions of the TEMPLATE (absent, unrenderable, unsourced variables) fail the whole request instead of appearing N times in `rejected` — they are one fact, identical for every client.
- **Replay is safe.** Each client's document takes `{idempotency_key}:{contact_id}`, so a retried or double-submitted batch returns the documents the first attempt queued. The platform page mints the key once per visit. Duplicate ids collapse for the same reason — a batch is a SET of clients.
- **Bounded at `MAX_BATCH_CLIENTS` (250).** The cost is not in the request (each document is a queued row the worker drains at its own pace), but the batch is one transaction, so an unbounded list would hold a pooled connection open for every insert.
- **A SAVEPOINT per client**, so one unanticipated constraint failure cannot abort the transaction and lose the documents already queued for everyone else.
- **The UI does not poll.** A single generate waits and hands the user the finished document; there is no equivalent for 24 of them, so the batch reports "N queued" plus the skipped clients BY NAME and stays put — a redirect to the library would throw away the only place a missed client is named. Platform: `/documents/create` (the "Several clients" mode of `generate-dialog.svelte`), `?/generateBatch`.
- **KNOWN LIMIT: per-client automations do not fire.** The action stream is one row per REQUEST, so a batch emits a single `document.batch_generated` rather than one `document.generated` per client — and a firm's "when a document is generated" rule is action-sourced on a `contact` subject, so it sees nothing for a batched client. Left deliberately: emitting per client would fire every such rule N times from one click, which for a rule that emails the client means 250 client emails from one button. Closing it properly means a per-document DOMAIN event on the flows outbox plus a decision about rate.

**The client's own book is a merge source (`accounts` / `portfolio`).** A document generated for a client carries that client's accounts, so an annual review or valuation letter needs no dataset to list them: `{% for row in accounts %}` loops the rows and `{{ portfolio.total_value }}`/`.account_count`/`.currency`/`.as_at` is the roll-up for prose. Four rules, each of which would be a wrong number in a client's hands:

- **Values are RAW** — numbers and ISO dates — and formatted at the point of use by the filters the engine already ships (`{{ row.value | currency }}`, `{{ row.opened | date }}`). A pre-formatted value makes its own filter a no-op, makes `sum_currency` impossible, and makes a date incomparable: an "as at" roll-up over "31 July 2026" and "30 June 2026" can only be resolved by string order, which is not date order.
- **`portfolio.as_at` is the OLDEST valuation across the rows**, not the newest — a roll-up is only as current as its stalest input.
- **A `draft` account is excluded**; `closed` is included. A draft is an account being set up rather than one the client holds, and an unopened account in a valuation table is a misstatement we would be posting. `status` is exposed so an author who wants only the live ones can say so.
- **A mixed-currency book withholds the symbol** (`portfolio.currency` is `""`) while still returning the total: a figure with no symbol beats the wrong symbol. An unvalued account keeps `value: null` rather than `0`, because 0.00 asserts the account is empty.

It is **not** a dataset and deliberately is not registered as one — no `documents.datasets` row, no uid, no version. Three places carry the vocabulary and move together: `app/render/sample.py::_SAMPLE_ACCOUNTS` (which IS the lint's catalogue, see the gate below), `app/worker.py::_client_accounts_context` (the real render), and `editor/variables.ts::BUILTIN_COLLECTIONS` (the editor). `tests/test_render_context.py` holds the anti-drift lock. In the editor, `collectionOptions()` offers it beside the firm's datasets in the repeat, chart and data-table source pickers, and its columns feed the live lint — so a mistyped `row.valu` inside an accounts loop is flagged as a dataset column would be. It gets the `row.*` half of the picker only: `one.<code>.<col>` is the first row of a resolved _dataset_, so `one.accounts.name` would be offered and render blank.

**Completeness gate:** generation refuses (422) a template whose variables have no data source — they would render blank — naming the offenders; the caller connects them to the input form / a linked dataset, or passes `allow_incomplete` to generate a deliberate draft anyway. The template editor surfaces the same check live (an "⚠ N unsourced variable(s)" indicator), so it rarely reaches generation. `app/routers/templates.py::unresolved_variables` is the shared check (preview lint, publish warning, generate gate); the client-side mirror is `editor/variables.ts::unsourcedTokens`, which is also dataset-column-aware for `row.<col>` accesses.

### 8.1 Generate UI (Svelte)

Three steps. Pick a template (grouped by `document_type`, loading its datasets and fixtures). Configure inputs (scalars from `input_schema` prefilled from fixtures; a subject-scope row picker; a collection/global dataset swap or CSV override). Generate (`POST /documents`, then poll `GET /documents/{uid}` at about 2s for up to about 120s; `generated` shows success and the document appears in the list, `failed` surfaces the error and offers retry). Define retry semantics: a retry on a `failed` doc re-enqueues a fresh `documents_render` job for the same document row, resets it `failed -> queued`, clears `error`, and is independent of the original DLQ entry (the DLQ message is for operator inspection, not auto-replayed). Decide a max manual-retry count or leave it unbounded.

---

## 9. Data model — core tables (Alembic + RLS)

These are authored as Alembic migrations in `apps/api/migrations`, following the `0001` portal conventions: internal `id int` PK + public `uid text UNIQUE`, an `int tenant_id` FK to `auth.tenants(id)` on every tenant-scoped table (shared with the Document Management spec, which owns `documents.documents`/`documents.files`), and the RLS policy shipped in the same migration (`FORCE ROW LEVEL SECURITY`, predicate `tenant_id = ANY (auth.tenant_lineage())` — the `0001` claim helper, **not** `app.current_org_id`).

The template/dataset tables here (`templates`, `template_versions`, `datasets`, `dataset_versions`) FK only `auth.tenants`/`auth.users`, so this half can land **before** `0002`. `documents.documents`/`documents.files` (owned by Document Management) bind to clients/accounts and wait on `0002`.

| Table                         | Purpose and key columns                                                                                                                                                                                                                                                                                                                                               |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `documents.templates`         | `uid`, `code` (unique per org), `name`, `document_type`, `description`, `current_published_version_id`, `archived_at`.                                                                                                                                                                                                                                                |
| `documents.template_versions` | Immutable once published. `version_number`, `status`, `renderer` (`weasy`/`chromium`, default `weasy`), `schema`/`input_schema`/`fixtures`/`metadata` (JSONB), `published_at`/`published_by`.                                                                                                                                                                         |
| `documents.datasets`          | `uid`, `code`, `name`, `format`, `scope`, `output_schema`, `parameters`, `enabled`.                                                                                                                                                                                                                                                                                   |
| `documents.dataset_versions`  | `version_number`, `status`, `format`, one of `catalogue_ref`/`inline_data`, `sample_data`, `published_at`.                                                                                                                                                                                                                                                            |
| `documents.documents`         | The generated record. `uid`, `template_version_id`, `document_type`, `status`, `source`, `inputs`, `dataset_snapshots`, `calculation_outputs`, `calc_lib_version`, `renderer_version`, `pdf_file_id`/`html_file_id`, `subject_ref` (polymorphic `(subject_type, subject_id)` — application/account/client; see binding note), `error`, `generated_at`/`generated_by`. |
| `documents.files`             | Path-addressed storage metadata (key scheme owned by Document Management, section 3): `bucket`, `storage_key`, `content_type`, `size_bytes`, `sha256` (integrity only, not a content-addressed key).                                                                                                                                                                  |

Postgres enums: `template_status` (`draft|published|archived`), `renderer` (`weasy|chromium`), `dataset_format` (`catalogue|inline`), `dataset_scope` (`subject|collection|global`), and `document_status` (`queued|generating|generated|pending_action|failed|superseded|archived`, owned by Document Management — see below).

Schema ownership. `documents.documents`, `documents.files`, and the `document_status` enum are owned by the [Document Management spec](./document-management.md), which defines the full column set (including `pending_action`, tasks, tags, shares) and the canonical migration. "Generation-only" means _only generation writes these columns_ — `template_version_id`, `inputs`, `dataset_snapshots`, `calculation_outputs`, `calc_lib_version`, `renderer_version`, `pdf_file_id`/`html_file_id`, `error`, `generated_at`/`generated_by` — not that generation writes _only_ these. Because generation inserts the row, it also writes the shared insert-time columns: `tenant_id`, `name` (from the template's `name`/`document_type`, unless overridden), `document_type`, `source='generated'`, `subject_ref`, `status`. Generation drives the `queued -> generating -> generated|failed` transitions; management owns `pending_action`/`superseded`/`archived`. This spec must not ship a competing migration for those tables.

The snapshot columns (`inputs`, `dataset_snapshots`, `calculation_outputs`) hold copies, not references, so regeneration is deterministic from them. `template_version_id`, not `template_id`, is the FK.

Subject binding. The prototype bound documents to accounts/applications and designed a CRM-contact binding. The portal pins a generated document to a polymorphic `subject_ref` — the column pair `(subject_type text, subject_id int)`, `subject_type` in `application | account | client`. **`subject_id` is `int`**, not uuid: its FK targets (`crm.applications`/`platform.accounts`/`crm.contacts`) are int-keyed in `0002`, and client-portal scoping matches `subject_id`/the contact link against the int `auth.contact_scope()`/`auth.account_scope()`. Document Management declares the identical column in the one shared `documents.documents` migration (it replaces the reference's separate `application_id`/`account_id`). The FKs are added in `0002`. Tasks, tags, shares, audit and schedules belong to adjacent systems (section 13), not this one.

---

## 10. Worker and queue

A new `DocumentWorker` in `apps/api` consumes the PGMQ `documents_render` queue (with a dead-letter queue). Its lifecycle is poll, claim, render, persist, ack. Visibility timeout is about 90s; after about 3 attempts a job goes to the DLQ and the document is marked `failed` with an `error`. On success the worker acks; on a retryable error it does nothing and lets the visibility timeout redeliver.

Each render sets status to `generating`, resolves datasets into rows and snapshots, merges `{ ...rows_by_code, ...inputs }`, injects tenant branding CSS, runs the Jinja render, substitutes markers, calls `renderer.to_pdf(...)`, uploads the PDF and HTML to the tenant bucket, and in one transaction updates `files`, `documents`, and the audit log.

Events / outbox. The "emit `document.generated`" step needs care: there is no events system in the portal yet (section 13, and the Document Management spec). Don't write to a non-existent events table. Use a **transactional outbox** — append a `document.generated` row to an outbox table inside the same render transaction, and let a separate dispatcher deliver it once notifications exist — or, for v1, drop to audit-only (a `generated` audit row) and add the outbox when the events system lands. Either way the worker transaction commits `files` + `documents` + audit atomically; event emission must not be a second, non-transactional side effect that can succeed or fail independently.

RLS and the worker. The worker is not an HTTP request, so it has no `Principal` and no `tenant_session`. There is **no `app.current_org_id` GUC** in this foundation — that was the old model. Follow the `0001` system pattern instead: open a transaction as **`service_role`** (BYPASSRLS — same as `app/db.py::system_transaction`) and stamp the job's `tenant_id` explicitly on every insert/update; _or_, if you want the worker's writes to stay RLS-checked, mint a minimal `request.jwt.claims` for the job (`{tenant_id, tenant_ids}`), `SET LOCAL ROLE authenticated`, and `set_config('request.jwt.claims', …, true)`. The job payload carries `tenant_id`. Do not run as the `migrator` role. Add a cross-tenant worker test before building on top.

This is the same class of problem as Document Management's public `/share/{token}` read (trusted DB access with no HTTP `Principal`), and the foundation already gives one strategy for both: `service_role` (BYPASSRLS) for worker jobs and the share `SECURITY DEFINER` function — exactly the mechanism `0001` uses for `auth.resolve_claims`/`audit.record`. Don't invent a second mechanism; reuse this one.

---

## 11. Determinism

Regeneration from `(template_version, inputs, dataset_snapshots, calc_lib_version, renderer_version)` must be reproducible: the same pinned inputs always produce a content-equivalent PDF, with the same layout, figures and charts. Pin everything that drives output: a fixed font set (no system-font autoload), seeded chart SVGs with stable ids, a PDF timestamp pinned to `generated_at` rather than `now()`, and `renderer_version` plus `calc_lib_version` recorded on each document. A later regen either reproduces the document or flags a version mismatch; it never differs silently.

Byte-identical output is a non-goal under WeasyPrint. True byte-for-byte reproduction is brittle there (font hinting, image encoders, library point releases), and it is not what the regulation needs. What matters is that a stored document can be re-derived from pinned inputs and that any drift is detected rather than swallowed. Recording `renderer_version` and `calc_lib_version` and rejecting a mismatch on regen meets that. If a stricter byte-identical guarantee is ever required, it argues for a pinned headless-Chromium binary over WeasyPrint (see section 15, Q3).

---

## 12. Permissions

The portal owns authorisation. WorkOS supplies identity and the `organisation_id`/role claims; the app enforces fine-grained permissions on top of RLS. Map this system onto the portal's permission style (for example `report.generate`):

- Template and dataset management (authoring, publishing, dataset CRUD): an admin/compliance-level permission such as `template.manage`.
- Generate: `document.generate` (advisers and above).
- View templates and documents: `template.view` / `document.view`, plus RLS row scoping (`see_all_in_org` for firm-wide visibility versus an adviser's own book).

Role and permission claims are null until WorkOS organisations are created, which is a documented portal next-step. Permission enforcement for this system can't be exercised fully until orgs and the authorisation layer land, so sequence the work accordingly.

---

## 13. Connections to adjacent systems

| Connection                                                   | Direction                                                                                           | Owner                                                                |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Document management (list, viewer, version history, archive) | Generation writes `documents.documents`; management surfaces it                                     | Document management (separate spec)                                  |
| Sharing (links/tokens, access log)                           | Acts on a generated document                                                                        | Document management                                                  |
| Tasks, tags, assignment                                      | Act on a generated document                                                                         | Document management                                                  |
| Clients / CRM (`subject_ref` binding)                        | Documents pin to an org-scoped client/account                                                       | Portal domain model (first table still being decided)                |
| Notifications / email                                        | Generation emits `document.generated`; delivery is separate                                         | Events/notifications (not yet built in the portal)                   |
| Object storage                                               | PDF/HTML written to a per-tenant bucket via signed URLs or scoped credentials                       | Storage (per-tenant bucket; not yet built)                           |
| Auth / RLS                                                   | `tenant_session` and permission checks on every route; per-job `service_role` context in the worker | Shared infra (`0001` built; `tenant_session` wired and claim-driven) |

---

## 14. Build order

Nothing exists yet, and several dependencies are portal-wide, so a workable sequence is:

1. Schema and RLS migration: the `documents.*` tables, enums, RLS policies, and a cross-tenant isolation test.
2. Storage: a per-tenant bucket and a signed-URL helper. Document download is a common cross-tenant leak, so enforce org scope in the endpoint as defence-in-depth on top of RLS. (Note: Document Management's stance is "RLS is the boundary, don't filter in app code." Reconcile the wording across both specs into one position — RLS is the mandatory floor; sensitive reads/downloads add an explicit scope check on top — so this doesn't read as a contradiction.)
3. Render pipeline in `apps/api`: lift the Jinja sandbox, calc library, substitution and WeasyPrint renderer from the prototype, and add the Python chart SVG.
4. Queue and worker: provision PGMQ and build `DocumentWorker` with per-job RLS context.
5. Generation endpoint: `POST /documents` and the poll route, wired to `tenant_session` and `document.generate`.
6. Template and dataset endpoints: CRUD, versions (including the draft-save route the prototype lacked), publish validation, preview, diff.
7. Svelte UI: the generate modal first, to prove the pipeline end to end, then the GrapesJS editor.

This follows the prototype's own staging (foundations, authoring, distribution), adapted to the portal's RLS-first setup.

---

## 15. Open questions

1. Chart SVG library: given the single-renderer decision (section 5.1), which Python library gives the cleanest deterministic SVG — matplotlib or altair/vl-convert? Note the determinism footgun (section 11): matplotlib embeds a creation date and a per-run element-id salt in SVG metadata by default, so deterministic output requires setting `svg.hashsalt` (stable ids) and suppressing the date (`metadata={'Date': None}`); vl-convert has its own id/version stamping. Whichever is chosen, pin its determinism settings explicitly.
2. Worker DB role and RLS context: confirm the §10 reconciled approach — `service_role` (BYPASSRLS) + explicit `tenant_id` stamping, vs minting per-job `request.jwt.claims` under `authenticated`. (The old `app.current_org_id` option is removed — it isn't part of the `0001` foundation.)
3. Renderer scope: WeasyPrint only for the prototype, or fix Chromium/Playwright (the prototype's Playwright path was broken anyway) for canvas and PDF/A?
4. Subject binding: pin documents to `clients`/accounts now, or wait for the CRM model to settle (sections 9 and 13)?
5. Editor scope: a full GrapesJS rebuild in Svelte, or a leaner raw HTML/CSS plus variable-schema editor first?
6. Calculation ownership: keep the Graphene calc library, or accept upstream figures for some document types?

---

## 17. Schema (DDL)

Tables owned by this spec only, reconciled to the `0001` foundation (see §0): internal `int` PK + public `uid text`, `tenant_id int` FK to `auth.tenants`, person refs `uuid` FK to `auth.users`, and claim-driven RLS (`tenant_id = ANY (auth.tenant_lineage())`). These four tables FK only `auth.*` + each other, so they can land **before** `0002`. `documents.documents` and `documents.files` are defined in the [Document Management spec](./document-management.md#13-schema-ddl) — generation writes to them but ships no competing migration. The request path runs as `authenticated`; `service_role` is BYPASSRLS (the worker); `migrator` runs DDL.

```sql
-- ── Enums ──────────────────────────────────────────────────────────────────
-- document_status and share_type are owned by document management.
CREATE TYPE documents.template_status AS ENUM ('draft', 'published', 'archived');
CREATE TYPE documents.renderer        AS ENUM ('weasy', 'chromium');
CREATE TYPE documents.dataset_source  AS ENUM ('csv', 'sql', 'static', 'questionnaire');
CREATE TYPE documents.dataset_scope   AS ENUM ('subject', 'collection', 'global');

-- ── templates ───────────────────────────────────────────────────────────────
-- current_published_version_id is a deferred FK back to template_versions to
-- allow both rows to be inserted in the same transaction on first publish.
CREATE TABLE documents.templates (
    id                           int                     PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    uid                          text                    NOT NULL UNIQUE,
    tenant_id                    int                     NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    code                         text                    NOT NULL,
    name                         text                    NOT NULL,
    document_type                text                    NOT NULL,
    description                  text,
    current_published_version_id int,    -- deferred FK added below
    created_by                   uuid                    NOT NULL REFERENCES auth.users(id),
    archived_at                  timestamptz,
    created_at                   timestamptz             NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code)
);
CREATE INDEX templates_tenant_type_idx ON documents.templates(tenant_id, document_type);

-- ── template_versions ─────────────────────────────────────────────────────────
-- Immutable once status = 'published'. The schema JSONB shape is in §5.2.
-- input_schema and fixtures are derived and stored on publish.
CREATE TABLE documents.template_versions (
    id             int                       PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    uid            text                      NOT NULL UNIQUE,
    tenant_id      int                       NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    template_id    int                       NOT NULL REFERENCES documents.templates(id) ON DELETE CASCADE,
    version_number integer                   NOT NULL,
    status         documents.template_status NOT NULL DEFAULT 'draft',
    renderer       documents.renderer        NOT NULL DEFAULT 'weasy',
    schema         jsonb                     NOT NULL,  -- {html, css, components[], datasets[], calculations[], variable_schema, gjs_project, pageSize}
    input_schema   jsonb,                               -- derived on publish: JSON Schema from variable_schema
    fixtures       jsonb,                               -- derived on publish: sample values for preview + form defaults
    metadata       jsonb,
    created_by     uuid                      NOT NULL REFERENCES auth.users(id),
    published_by   uuid                      REFERENCES auth.users(id),
    published_at   timestamptz,
    created_at     timestamptz               NOT NULL DEFAULT now(),
    UNIQUE (template_id, version_number)
);
CREATE INDEX template_versions_template_idx ON documents.template_versions(template_id);

-- Deferred FK templates → template_versions (circular).
ALTER TABLE documents.templates
    ADD CONSTRAINT fk_current_published_version
    FOREIGN KEY (current_published_version_id)
    REFERENCES documents.template_versions(id)
    DEFERRABLE INITIALLY DEFERRED;

-- FK documents.documents.template_version_id → template_versions (column owned by
-- the DM spec). Add in the same migration that creates template_versions.
ALTER TABLE documents.documents
    ADD CONSTRAINT fk_template_version
    FOREIGN KEY (template_version_id) REFERENCES documents.template_versions(id);

-- ── datasets ────────────────────────────────────────────────────────────────
CREATE TABLE documents.datasets (
    id            int                      PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    uid           text                     NOT NULL UNIQUE,
    tenant_id     int                      NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    code          text                     NOT NULL,
    name          text                     NOT NULL,
    source_type   documents.dataset_source NOT NULL,
    scope         documents.dataset_scope  NOT NULL,
    output_schema jsonb,
    parameters    jsonb,
    enabled       boolean                  NOT NULL DEFAULT true,
    created_by    uuid                     NOT NULL REFERENCES auth.users(id),
    created_at    timestamptz              NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code)
);
CREATE INDEX datasets_tenant_idx ON documents.datasets(tenant_id);

-- ── dataset_versions ──────────────────────────────────────────────────────────
-- source_type is denormalised from datasets so the exactly-one-source CHECK needs
-- no join. Immutable once published. Reuses template_status (draft|published|archived).
CREATE TABLE documents.dataset_versions (
    id                int                       PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    uid               text                      NOT NULL UNIQUE,
    tenant_id         int                       NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    dataset_id        int                       NOT NULL REFERENCES documents.datasets(id) ON DELETE CASCADE,
    version_number    integer                   NOT NULL,
    status            documents.template_status NOT NULL DEFAULT 'draft',
    source_type       documents.dataset_source  NOT NULL,  -- denormalised for CHECK below
    csv_file_id       int                       REFERENCES documents.files(id),
    sql_query         text,
    static_data       jsonb,
    questionnaire_uid text,
    created_by        uuid                      NOT NULL REFERENCES auth.users(id),
    published_at      timestamptz,
    created_at        timestamptz               NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, version_number),
    CHECK (
        (source_type = 'csv'           AND csv_file_id       IS NOT NULL AND sql_query IS NULL AND static_data IS NULL AND questionnaire_uid IS NULL) OR
        (source_type = 'sql'           AND sql_query         IS NOT NULL AND csv_file_id IS NULL AND static_data IS NULL AND questionnaire_uid IS NULL) OR
        (source_type = 'static'        AND static_data       IS NOT NULL AND csv_file_id IS NULL AND sql_query IS NULL AND questionnaire_uid IS NULL) OR
        (source_type = 'questionnaire' AND questionnaire_uid IS NOT NULL AND csv_file_id IS NULL AND sql_query IS NULL AND static_data IS NULL)
    )
);
CREATE INDEX dataset_versions_dataset_idx ON documents.dataset_versions(dataset_id);

-- ── RLS + grants (claim-driven, per 0001) ────────────────────────────────────
-- Authoring tables are staff-scoped; no client branch (clients don't author
-- templates). Read-all staff see the firm; own-book advisers see their tenant too
-- (templates/datasets are firm assets, not client-scoped).
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['templates','template_versions','datasets','dataset_versions'] LOOP
    EXECUTE format('ALTER TABLE documents.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE documents.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY %I_tenant ON documents.%I FOR ALL TO authenticated '
      'USING (tenant_id = ANY (auth.tenant_lineage())) '
      'WITH CHECK (tenant_id = ANY (auth.tenant_lineage()))', t, t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON documents.%I TO authenticated', t);
    EXECUTE format('GRANT ALL ON documents.%I TO service_role', t);
  END LOOP;
END $$;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA documents TO authenticated, service_role;

-- DML audit trail (free, from 0001): captures every write incl. the publish worker.
SELECT audit.attach(ARRAY['documents.templates','documents.template_versions','documents.datasets','documents.dataset_versions']);

-- ── PGMQ queue (provisioned once, not per-migration) ─────────────────────────
-- After the pgmq extension is enabled:
-- SELECT pgmq.create('documents_render');
-- SELECT pgmq.create('documents_render_dlq');

-- ── Worker RLS context (per §0 / §10) ─────────────────────────────────────────
-- The DocumentWorker has no HTTP Principal. There is NO app.current_org_id GUC in
-- this foundation. Follow the 0001 system pattern — connect as service_role
-- (BYPASSRLS, like app/db.py::system_transaction) and stamp the job's tenant_id
-- explicitly on every write; OR mint a minimal request.jwt.claims for the job and
-- SET LOCAL ROLE authenticated. Never run the worker as the migrator role.
--   await conn.execute(text("SET LOCAL ROLE service_role"))   -- then stamp tenant_id per write
```

---

## 16. Out of scope

Document management, sharing, tasks and tags, and the clients/CRM model (adjacent — section 13). Scheduled and batch generation. EIS/IHT illustrations, cash-flow modelling, signature workflows, collaborative template editing, AI-generated content, PDF/A-3, and out-of-band PDF merge. Questionnaire-sourced datasets are modelled but not wired end to end.
