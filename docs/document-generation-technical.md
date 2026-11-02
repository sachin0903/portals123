# Document Generation System — Technical Requirements

## 1) Purpose

Configurable, multi-tenant document generation system producing financial documents (HTML + PDF):

- **Illustrations** — investment projections, growth scenarios, allocation breakdowns
- **Valuations** — portfolio snapshots with holdings, performance, market values
- **Drawdown Illustrations** — retirement income projections with withdrawal scenarios
- **Periodic Reports** — quarterly/annual statements and performance
- **Suitability Reports** — compliance documentation combining questionnaire outputs with recommendations

Must be versioned, auditable, and integrate with existing platform application.

## 2) Goals and non-goals

### Goals

- Define, version, and publish **document templates** per tenant (optionally per platform/product)
- **Dual-output rendering** — same template renders to both HTML (viewing) and PDF (download/archival)
- **HTML-to-PDF** via Playwright (Node) or WeasyPrint (Python) — abstracted behind a common interface
- **Immutable template versioning** — draft → published → archived
- **On-demand** and **scheduled** generation (periodic reports)
- **Configurable data sources** — DB queries, static datasets, questionnaire outputs
- **Deterministic calculations** — projections, valuations, scenario modeling
- **Server-side charts/tables** embedded in output
- **AI-generated content** — narrative text driven by structured data, configurable prompts, per-tenant tone
- **MinIO storage** with full audit trail (documents, dataset snapshots)
- **Preview mode** for template authors

### Non-goals (initial release)

- WYSIWYG visual designer (html/code-defined templates first)
- Arbitrary JS/Python execution in templates
- Custom fonts beyond standard PDF fonts

## 3) Users and permissions

Aligned with existing `platform.role_type`:

- **Admin** — create/edit/publish templates, configure datasets/calculations, manage schedules, preview
- **Adviser** — generate documents, download, view history
- **Client** — view/download via unique links in portal app (no login required)
- **Support** — view history (tenant scoped), regenerate, access logs

## 4) Functional requirements

### 4.1 Templates

**Types:** `illustration`, `drawdown_illustration`, `valuation`, `quarterly_report`, `suitability_report`

**Scoping:** Always tenant. Optionally platform/product.

**Versioning:** Immutable once published. Clone published → new draft. Generated docs point to exact version used.

**Status:** `draft` → `published` → `archived`

### 4.2 Parameters

Zod schema per template defining required inputs (subject IDs, date ranges, scenarios, options). Validated before job creation. Support defaults.

### 4.3 Datasets

Data sources resolved at generation time. Three types:

| Type              | Description                                                               |
| ----------------- | ------------------------------------------------------------------------- |
| **Database**      | Parameterised queries, auto tenant-scoped                                 |
| **Static**        | Inline data versioned with template (assumptions, fee schedules, lookups) |
| **Questionnaire** | Submitted answers and computed outputs (risk scores, suitability)         |

**Management:** First-class entities — central registry per tenant, versioned independently of templates, Zod-validated output schemas, tenant isolation enforced automatically, independently testable via admin API. Database datasets use registered SQL (DB views preferred, or `dataset_queries` table) — never inline SQL.

**Output shapes:** Flat rows (tables/lists) or keyed maps (random-access lookup).

**Execution:** Parallel where safe, cached per run, snapshots stored for audit, wide queries to avoid N+1.

#### 4.3.1 Scopes and iteration

Each request defines a **subject** (type + ID). Datasets declare scope: `subject` (single entity), `collection` (list, e.g. all accounts for client), or `global` (tenant-wide). Collection datasets must be keyed/stable for iteration without N+1.

### 4.4 Calculations

Controlled function library (no arbitrary code). Each calculation references a function by key, receives inputs via Jinja interpolation, produces validated output.

**Example functions:** `compound_growth`, `drawdown`, `fee_impact`, `allocation_breakdown`, `performance_attribution`, `scenario_comparison`

Dependency-ordered execution, no circular deps (validated at publish), deterministic, outputs stored for audit.

### 4.5 Visualizations

Server-side rendered using Recharts (already installed) + React → inline SVG.

**Types:** `line_chart`, `bar_chart`, `pie_chart`, `stacked_area_chart`, `table`

### 4.6 Template structure

Templates organised as **pages** → **sections** → **blocks**. Pages control layout boundaries; sections control content flow.

**Structure:** metadata, parameters (Zod), datasets, calculations, visualizations, pages[], layout (page size, orientation, margins, header/footer)

**Page types:** `cover`, `content`, `footer`

**Block types:** `text`, `key_value_pairs`, `visualization`, `table`, `component`, `ai_content`, `repeat`, `html_block`

**Interpolation:** Jinja-style `{{ path.to.data }}` with filters (`currency`, `percentage`, `date`, `number`, `join`, `truncate`, `lower`). No arbitrary expressions — heavy logic lives in datasets/calculations.

**Components:** Reusable block registry (e.g. `performance_summary`, `transactions_table`). Components bind to datasets via Jinja expressions. Must not access DB directly — all data via template-defined datasets.

**Iteration:** `repeat` block with `items` (Jinja expression → array), `as` (binding name), nested blocks. Supports multi-level nesting (client → models → accounts). Grouping should come from dataset transforms/calculations, not template scripting.

**Preview:** Sample data fixtures stored with templates. Real-data preview enforces RBAC, redacts PII in logs, watermarks output. Bounded preview scope to prevent huge runs.

### 4.7 Rendering

**Dual output** from same template definition:

- **HTML** — primary target, self-contained, stored in MinIO for fast retrieval. Used for: adviser preview, client portal viewer, in-app embedding, email "view online" links
- **PDF** — generated from HTML via configured renderer. Used for: download, email attachment, archival, compliance

#### 4.7.1 HTML rendering

Server-side React → static HTML. Tenant branding via CSS custom properties. Responsive for screen, print-ready CSS for browser print. All assets inlined or signed URLs.

#### 4.7.2 PDF rendering

Two options, abstracted behind `DocumentRenderer` interface:

|             | Playwright (Node)                                                | WeasyPrint (Python)                                |
| ----------- | ---------------------------------------------------------------- | -------------------------------------------------- |
| Approach    | Headless Chromium `page.pdf()`                                   | CSS Paged Media (Pango/Cairo), v68.1               |
| Performance | ~0.7-1s/page                                                     | ~3-10s simple docs                                 |
| Strengths   | Native browser rendering, single runtime, charts render natively | Superior `@page`/margin boxes/counters, CMYK/PDF/X |
| Weaknesses  | CPU-bound, ~400MB Chromium binary                                | Python sidecar, heavier operational overhead       |

#### 4.7.3 Pipeline

1. Resolve datasets (parallel) → 2. Execute calculations (dependency order) → 3. React → static HTML + inline SVG → 4. Inject branding → 5. Store HTML → 6. Generate PDF (if requested) → 7. Assemble (merge attachments via `pdf-lib`)

Renderer is a **deployment config**, not a template property. Templates stay renderer-agnostic.

### 4.8 Bulk generation and queuing

Document generation is **asynchronous**. Single requests and bulk/scheduled runs use the same queue infrastructure (priority/default).

**Queue:** PostgreSQL-native using **PGMQ** (lightweight, Postgres-only message queue). Messages are durable, transactional, and co-located with application data.

**Why PGMQ over alternatives:**

- Single infrastructure dependency (Postgres) — no Redis/RabbitMQ to operate
- Transactional enqueue — generation run record + queue message in same transaction (no orphans)
- Visibility timeout + dead-letter queue built in
- Lightweight — Postgres extension, not a separate service
- Adequate throughput for document generation workloads (not high-frequency trading)

**Single generation flow:**

1. Validate parameters and permissions
2. Create `document` record (status: `queued`)
3. Enqueue message to PGMQ in same transaction (priority queue)
4. Worker reads message, updates status to `running`, executes pipeline
5. On success: store artifacts (html/pdf), update status to `succeeded`, emit event
6. On failure: update status to `failed`, message goes to dead-letter queue after retries (dependant on error)

**Bulk/scheduled generation:**

- Scheduler creates one generation run + queue message per subject matching scope
- Messages processed concurrently by worker pool (configurable concurrency)
- Backpressure via PGMQ visibility timeout — workers only pull what they can handle
- Progress tracking: query `runs` for batch status (count by status)
- Batch summary event emitted when all runs for a schedule complete

**Concurrency and resource management:**

- Worker pool size = configurable (default: CPU cores - 1 for Playwright, higher for WeasyPrint)
- Each worker pulls one message at a time from PGMQ
- Cache templates for efficiency (LRU cache)
- Per-job timeout (60s default) prevents runaway renders
- Playwright: share one browser instance across workers, recycle every N jobs (~100)
- WeasyPrint: worker recycling after N jobs for memory safety

**Idempotency:** Optional `idempotencyKey` per request. Duplicate key (same tenant) returns existing run instead of creating new one.

**Error handling:**

- Transient failures (DB timeout, MinIO unavailable): automatic retry via PGMQ visibility timeout (3 retries, exponential backoff)
- Permanent failures (invalid template, missing data): mark as `failed`, move to dead-letter queue
- Dead-letter queue for manual investigation/retry

### 4.9 Scheduling

Periodic report generation. Each schedule: tenant, template (uses latest published version), frequency (quarterly/monthly/annual/custom cron), timezone, scope (entity type + filters), parameters template (Jinja for schedule-time values), notification settings.

Scheduler checks due schedules periodically, creates generation jobs for matching subjects (feeds into 4.8 bulk queue).

### 4.10 Preview and validation

Before publishing: validate schema, dependencies, references, circular deps, variable resolution. Preview with sample data (fixtures) or real data (RBAC-enforced, watermarked). Returns HTML for in-app preview or PDF.

### 4.11 Audit and reproducibility

Store per generation run: template version checksum, parameters, dataset hashes + full snapshots (inline JSONB < 1MB, MinIO for larger), calculation outputs, who/when/duration.

**Retention:** Regulated documents 7 years, previews 30 days, configurable per tenant. PII redaction before snapshot storage. Schema versioning on snapshots for future interpretation.

**Reproducibility:** "Regenerate" uses stored snapshots (not live data). Optional live-data regenerate + diff for compliance.

### 4.12 Page builder and PDF assembly

**3-layer strategy:**

1. **Code-defined components (MVP)** — React components driven by template JSON
2. **Custom HTML/CSS blocks** — `html_block` for bespoke pages (sanitised, no scripts, CSS allowlist)
3. **Visual builder (future)** — outputs JSON schema referencing component registry

**PDF assembly:** Template defines ordered parts (`generated` sections + `attachment` PDFs from MinIO). Merged via `pdf-lib`. Optional TOC and page numbering. Each artifact stored for audit.

**Branding:** Auto-injected → CSS custom properties. Components use variables, not hardcoded values. Logo, colours, typography, contact details, regulatory text.

## 5) UI requirements

### 5.1 Admin UI (platform)

Under "Documents" in `apps/platform`:

- Template list with filters, create/edit, version history
- JSON editor with tabs (Parameters, Datasets, Calculations, Visualizations, Sections), real-time validation, preview
- Publish flow (validate → confirm → immutable)
- Schedule management (list, create/edit, enable/disable, run now, history)

### 5.2 Client Portal

`apps/portal` (separate from onboarding):

- Unique links (signed tokens with expiry) — no login required
- **HTML viewer** as primary experience, PDF download alongside
- Document list grouped by type and date

### 5.3 Adviser interface (platform UI)

- "Generate Document" button on application/account pages → modal (type selector, parameter form, generate)
- In-app HTML preview, progress indicator, download + share link on completion
- Document history table with view (HTML), download (PDF), share link, regenerate

## 6) Calculation function library

| Function                  | Description                                      |
| ------------------------- | ------------------------------------------------ |
| `compound_growth`         | Growth with optional regular contributions       |
| `drawdown`                | Retirement income with withdrawals and inflation |
| `fee_impact`              | Fee drag over time (platform, advice, fund)      |
| `allocation_breakdown`    | Allocation percentages by category               |
| `performance_attribution` | Returns by asset class/fund                      |
| `scenario_comparison`     | Side-by-side growth scenarios                    |

All deterministic, Zod-validated, year-by-year breakdowns where applicable.

## 7) Security and privacy

- **Tenant isolation** at all layers (DB queries, storage paths)
- **Access control** — advisers for own tenant only, clients via signed tokens
- **Data minimization** — only fetch required fields
- **Encryption** — MinIO at rest, HTTPS in transit
- **Signed URLs** with expiry for downloads
- **Audit trail** — all generation requests, publishes, schedule changes
- **Rate limiting** per user/tenant

## 8) Performance

- Generation target: < 10s for typical docs (< 10 pages, 3-5 charts)
- Concurrent generation via PGMQ worker pool (vertical / horizontal scaling - CPU/threads / # workers)
- Caching: static datasets per run, tenant branding globally
- Resource limits per worker

## 9) Implementation phases

1. **Core (MVP)** — tables, template CRUD, generation endpoint + PGMQ queue, one dataset type (DB), one calculation (compound growth), HTML+PDF rendering, one doc type (illustration)
2. **Calculations & Viz** — expanded calculation library, multiple chart types, server-side rendering, parallel datasets, preview mode
3. **Document Types** — valuations, suitability reports, periodic reports
4. **Scheduling** — schedule config, cron runner, bulk generation, notifications
5. **Advanced** — template cloning/comparison, regeneration with snapshots, enhanced UI

## 10) Testing strategy

- **Unit:** dataset logic, calculations, template validation, interpolation, filters
- **Integration:** end-to-end generation flow, versioning immutability, idempotency, tenant isolation
- **Snapshot:** HTML/PDF regression testing (visual diffs or text extraction)
- **Load:** concurrent generation, queue backpressure, DB performance

## 11) Decisions

| Decision     | Choice                                                   | Rationale                                                                             |
| ------------ | -------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Rendering    | Dual-output (HTML + PDF)                                 | HTML for viewing, PDF for download/archival                                           |
| PDF renderer | Playwright (node) or WeasyPrint (python)                 | Abstracted interface; Playwright for simplicity, WeasyPrint for CSS Paged Media/PDF/X |
| Queue        | PGMQ (Postgres-native)                                   | Single infra dependency, transactional enqueue, no Redis                              |
| Charts       | Recharts + server-side React → SVG                       | Consistent with existing UI                                                           |
| Datasets     | Registered SQL                                           | Reviewable, testable, decoupled from templates                                        |
| Page builder | 3-layer (code components → HTML blocks → visual builder) | Progressive complexity                                                                |

### Questions

- **Visual builder library** — GrapesJS vs custom. Defer to Phase 5.
- **Scheduling infra** - cron style scheduling

## 12) Success criteria (MVP)

- [ ] Admin can create and publish an illustration template
- [ ] Template includes datasets, calculations, and visualizations
- [ ] Adviser can generate illustration (HTML + PDF)
- [ ] HTML viewable in platform UI and client portal
- [ ] PDF downloadable with tenant branding
- [ ] At least one chart and one table in output
- [ ] Async generation with status tracking
- [ ] Documents stored in MinIO, metadata in postgresql with audit trail
- [ ] Dataset snapshots stored per run
- [ ] Immutable template versions
- [ ] Tenant isolation verified
- [ ] Preview mode works
