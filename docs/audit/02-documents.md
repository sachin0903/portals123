# 2 · Documents — library, upload, generate, send, requests, shares

Portal: **platform + client**. Part of the [functional audit](README.md) — statuses: ✅ working · ⚠️ partial · 🎭 demo/mock · ❌ missing · 💥 broken

---

Scope traced: `apps/platform/src/routes/documents/**`, `routes/files/**`, `lib/components/documents/**`, `lib/documents.ts`, `lib/demo.ts`; API `app/routers/documents.py`, `files.py`, `shares.py`, `forms.py`, `app/worker.py`, `app/flows/orchestrator.py`; migrations `0005/0006/0008/0010/0013/0015/0017/0018`; intent per `docs/document-management.md`.

## Documents list (`routes/documents/`)

### Library table (list + drill-in)

- **Status**: ⚠️
- **What it does today**: `/Users/gio/gpl/portals/apps/platform/src/routes/documents/+page.server.ts:30-35` fans out to `GET /documents`, `GET /files`, `GET /documents/clients`, `GET /documents/activity?limit=100`, then merges documents and attachments into one `DocumentRow[]` via `toRow`/`fileToRow` (`/Users/gio/gpl/portals/apps/platform/src/lib/documents.ts:248,295`). The API list (`/Users/gio/gpl/portals/apps/api/app/routers/documents.py:209`) is RLS-scoped and excludes `superseded`/`archived`. The load computes `loadError` (`+page.server.ts:38`) with a comment promising a retry banner, but `+page.svelte` never reads it — a failed/401 fetch renders as the "No documents match your filters" empty state.
- **Expected**: As an adviser I open the library and see every document and supporting file my firm holds, with a clear failure state when the API is down.
  - Merged list of `documents.documents` + `documents.attachments`, newest first.
  - A transient API failure shows a retry banner, never an empty list.
  - Archived documents reachable through an explicit "Archived" view.
  - Row count/total shown so pagination is trustworthy.
  - Queued/failed generations visibly distinguished from live documents.
- **Gaps**:
  - `loadError` is computed and discarded → silent data loss on 401/5xx (looks like "no documents").
  - No archived view and no unarchive endpoint: `POST /{uid}/archive` (`documents.py:1576`) is one-way from the UI's perspective.
  - Whole tenant payload fetched then paginated client-side (`+page.svelte:151-154`); no server paging/limit.
  - `queued`/`generating`/`failed` documents appear as ordinary rows with 0 bytes and a broken preview; no status column at all in the table.

### List filters, search and column sorting

- **Status**: ✅
- **What it does today**: Search by name, a documents/files kind filter and a type drill-in are applied in `visible` (`/Users/gio/gpl/portals/apps/platform/src/routes/documents/+page.svelte:126-148`); column headers sort via `setSort`/`sortValue` (`:315-338`); every filter change resets to page 1 (`:155-161`). All purely client-side over the already-loaded rows.
- **Expected**: As an adviser I narrow a large library quickly and can share the narrowed view.
  - Name search, kind, type, client and date-range facets.
  - Sort by every visible column, direction toggles.
  - Filter state reflected in the URL so a view is linkable/refresh-safe.
  - Active filters visibly summarised with a clear-all.
- **Gaps**: no client, status, date or author facets despite those columns being sortable; no URL/state persistence (a refresh or a Back from a document loses the filter); search is substring-on-name only — no template name, client or content match.

### Type drill-in / "folders" metaphor

- **Status**: ⚠️
- **What it does today**: Clicking a row's type sets `activeFolder` (`+page.svelte:724`) which filters `visible` client-side and renders a breadcrumb (`:757-770`). There are no folder tiles, no folder entity, no nesting — `document_type` is a flat free-text column (`0005_documents_storage.sql:53`, no CHECK).
- **Expected**: As an adviser I browse by category and can put a document where I expect to find it.
  - Category browse with per-category counts.
  - One category vocabulary across upload, drill-in, retype and detail.
  - Deep-linkable category views.
- **Gaps**: "folders" are a client-side filter over a flat string, not a hierarchy — no counts, no nesting, no deep link; `folderLabel` (`+page.svelte:95`) falls through to the raw DB string for anything outside `DOC_TYPE_META`, so uploaded documents display raw `report`/`tax`/`other`.

### Drag-to-move / "Change type"

- **Status**: ⚠️
- **What it does today**: Row drag sets `application/x-doc-uid`, a floating chip strip renders drop targets, and `onFolderDrop` → `moveTo` posts to the `retype` action (`+page.svelte:393-422`), which PATCHes `document_type` (`+page.server.ts:123-139` → `documents.py:1542`). It persists and audits `document.updated`. But the chips and the kebab submenu are built from `DOC_TYPES = Object.keys(DOC_TYPE_META)` — the **template** taxonomy `illustration · suitability · valuation · review · factfind · statement` (`/Users/gio/gpl/portals/packages/ui/src/lib/templates/types.ts:34`) — whereas the upload dialog and the detail editor use `report · statement · agreement · identity · tax · other` (`upload-dialog.svelte:36-44`, `[id]/+page.svelte:95`).
- **Expected**: As an adviser I reclassify a document and it stays consistent everywhere.
  - Drag or menu retype persists and audits.
  - One vocabulary, offered identically at upload, retype and edit.
  - The current type is excluded from the target list.
  - Undo or at least a toast naming the old type.
- **Gaps**:
  - **Two disjoint taxonomies.** Retyping writes a value the upload dialog and detail editor cannot represent; `DOC_TYPES.filter(t => t !== doc.category)` never excludes anything for uploaded docs because the vocabularies don't intersect.
  - `document_type` is unvalidated free text server-side — the API accepts any string.
  - Files are silently non-droppable (`onRowDragStart` early-returns, `:406`) with no affordance explaining why.

### Upload flow

- **Status**: ⚠️
- **What it does today**: `upload-dialog.svelte` posts to `/documents?/upload` or `/files?/upload` depending on the Document/Supporting-file switch (`:66-98`). The document branch forwards multipart to `POST /documents/upload` (`+page.server.ts:56-80` → `documents.py:237`), which magic-byte sniffs, stores the blob, inserts `documents.files` + `documents.documents` (`status='generated'`, `source='uploaded'`) and audits `document.uploaded`. OS drag-and-drop onto the card pre-fills the dropzone (`+page.svelte:427-433`).
- **Expected**: As an adviser I drop one or several files and they all land in the library, correctly classified.
  - Every accepted file in the dropzone is uploaded.
  - Rejected files are named with the reason.
  - Classification is either genuinely automatic or an honest manual choice.
  - Optional client binding at upload time.
  - Per-file progress and a partial-failure report.
- **Gaps**:
  - **Multi-file drops silently lose files**: `FileDropzone` binds a `File[]` but only `files[0]` is posted (`upload-dialog.svelte:71`); drop three, two vanish with a success toast.
  - "Auto-classify" is theatre — it maps to `document_type: 'other'` (`upload-dialog.svelte:79`); the API has no classifier despite `docs/document-management.md` listing "auto-classify" as built.
  - No client picker on the **document** branch, so an uploaded document has no client until a separate Send; the file branch has one.
  - No progress, no per-file result, no supersede-existing option (`documents.superseded_by` exists at `0005_documents_storage.sql:51` and is never written by any code path).

### Generate flow

- **Status**: ✅
- **What it does today**: Reached only from Templates (`routes/templates/+page.svelte:115`, `templates/[id]/+page.svelte:219`) → `/documents/create?template=…`, which loads the template, its published input-form snapshot, clients and referenced variables (`create/+page.server.ts:18-76`). Generate posts to `POST /documents/generate` (`create/+page.server.ts:97` → `documents.py:334`), which validates the published version has HTML, runs the completeness gate, inserts a `queued` document and a `documents.render_jobs` row in one transaction, and audits `document.queued`. `create/+page.svelte:28-40` then polls `/documents/{uid}/status` (`[id]/status/+server.ts`) every 2s for ~2 min; the `DocumentWorker` (`/Users/gio/gpl/portals/apps/api/app/worker.py:290,320`) claims the job `FOR UPDATE SKIP LOCKED`, renders, uploads the PDF, flips the document to `generated` and audits `document.generated`, retrying/dead-lettering on failure.
- **Expected**: As an adviser I generate a document from a template and land on the finished PDF.
  - Enqueue is atomic (document + job) and returns immediately.
  - Progress is visible; success navigates to the document.
  - A render failure is surfaced with a reason and is retryable.
  - The pipeline degrades honestly when the worker is down.
- **Gaps**:
  - **Worker-down is indistinguishable from slow.** The worker is a separate deployment unit (`compose.dev.yaml:57-68`, "Start: `pnpm worker:up`"). With it stopped, the poll times out and `create/+page.svelte:42` navigates to `/documents` with no message; the document sits `queued` forever, listed as an ordinary row with a 404 preview. No queue-depth/worker-liveness signal anywhere in the UI.
  - No retry/requeue action for a `failed` document — no endpoint and no UI; the row is a dead end.
  - No Generate entry point on the documents page itself (only via Templates), so the library's primary creation verb is absent from the library.
  - The poll fires 60 unconditional round-trips; `s.ok === false` is swallowed (`:31`), so a persistent 403 looks like "still rendering".

### Send flow

- **Status**: ⚠️
- **What it does today**: `send-dialog.svelte:81-103` posts to `/documents?/send` (`+page.server.ts:83-104`) → `POST /documents/{uid}/send` (`documents.py:486`), which validates recipients against RLS-visible `crm.contacts`, inserts `documents.document_contacts` rows with `role='recipient'` (granting the client portal visibility), and writes one `document.sent` audit event **per recipient**. That part is solid. However `SendDocument.message` (`documents.py:478`) is never read, and no email is sent anywhere — the only mail paths in the API are `app/tasks/notifier.py` and `app/flows/notifications.py`, neither of which handles document sends. The success screen nonetheless states "Recipients will be notified by email" (`send-dialog.svelte:144`).
- **Expected**: As an adviser I send a document to clients and they are told about it.
  - Recipients gain portal visibility (`document_contacts`).
  - Each recipient is emailed, with the covering note.
  - The note is persisted and visible to the client.
  - Send is audited per recipient; the UI reports partial failures.
- **Gaps**:
  - **The covering message is silently discarded** — collected, posted, parsed into the Pydantic model, never stored or delivered.
  - **No email is sent, but the UI promises one.** The client only discovers the document by logging in.
  - Partial success is invisible: `SendResult.sent` counts only rows the API accepted, but the dialog displays `selected.size` (`send-dialog.svelte:93`), so silently-dropped recipients still read as sent.
  - No send affordance on the document detail page — you must return to the list.

### Bulk archive / bulk delete

- **Status**: ✅
- **What it does today**: Multi-select with shift-ranges (`+page.svelte:342-361`) splits into `selectedDocs`/`selectedFiles` (`:362-367`); the bulk bar routes through `ConfirmDialog` then `archive()` / `deleteFiles()` (`:259-295`), which loop per-uid, **check `res.ok`**, count failures, `invalidateAll()` and toast accurately. Archive → `POST /documents/{uid}/archive` (soft, `documents.py:1576`); delete → `DELETE /files/{uid}` (soft, `files.py:291`).
- **Expected**: As an adviser I clear several rows at once and know exactly what happened.
  - Confirmation naming the count and the irreversibility.
  - Per-item result; partial failures named.
  - Documents archive (recoverable), files delete.
  - The list refreshes to match.
- **Gaps**: N+1 requests with no batch endpoint (50 selected = 50 round-trips, no cancel, no progress); failure toast gives a count but never says _which_ rows failed; the delete confirmation says "This cannot be undone" though `files.py:291` is a soft delete (`deleted_at`) — with no restore path either way.

### Per-row actions (kebab)

- **Status**: ⚠️
- **What it does today**: `rowMenu` (`+page.svelte:630-670`) offers Download/Delete for files and View/Share/Resend/Change type/Archive for documents. All targets are real except the label logic: `getShareForDoc(doc.uid)` (`:650`) reads the **hardcoded demo array** at `/Users/gio/gpl/portals/apps/platform/src/lib/demo.ts:258-296`, keyed on demo uids like `doc-anr-2025`, so it can never match a real `doc_<hex>`.
- **Expected**: As an adviser the row menu reflects the document's real state.
  - "Manage share link" only when a live share exists.
  - Resend pre-fills the previous recipients.
  - Menu items hidden when the user lacks permission.
- **Gaps**: the share label always reads "Share link…" even when an active `documents.shares` row exists (demo-data lookup); "Resend" calls `openSend({ documentUid })` with no `recipientContactId` (`:652`), so it is just Send with the document pre-picked; document actions (Share/Resend/Change type/Archive) are not gated on `data.canManage` unlike the file actions (`:643`).

### Activity rail

- **Status**: ✅
- **What it does today**: Fed by `GET /documents/activity?limit=100` (`documents.py:583`) reading `documents.audit_log` with per-recipient scoping for `document.sent`, live share-URL resolution (`_with_share_url`, `:570`) and stable `created_at DESC, id DESC` ordering. The rail groups near-identical events (`lib/documents.ts:145`), maps verbs to label/tone/icon via `ACTIVITY_META` (`:190`), filters by event type and by person (adviser actor or client recipient) and expands groups in place (`+page.svelte:521-610`).
- **Expected**: As an adviser I see what has happened to my clients' documents, filterable.
  - Real events from the audit log, newest first.
  - Every verb the API can emit has a label.
  - Filter by event type and by person.
  - Deep links to the document.
- **Gaps**: hard 100-event ceiling with no paging or "load older" — "View all activity (N)" only expands what is already loaded; the type filter is client-side over that same 100, so switching on "Viewed" can surface _fewer_ recent events than expected; no date range; `share.accessed` rows render the raw share URL as the link label.

### `CURRENT_USER` hardcoded filter

- **Status**: 🎭
- **What it does today**: `const CURRENT_USER = 'James Okafor'` (`+page.svelte:81`) drives `myRows = rows.filter(d => d.scope === 'team' || d.createdBy === CURRENT_USER)` (`:124`). Because `toRow`/`fileToRow` hardcode `scope: 'team'` (`lib/documents.ts:256`, `:305`) — the API has no scope concept at all — the predicate is always true. It filters nothing today.
- **Expected**: As an adviser I see team documents plus my own private drafts, resolved from my session.
  - Ownership resolved from `locals.user`, never a literal.
  - Private vs team scope is a real, persisted column.
  - A private document is invisible to colleagues (enforced by RLS, not a client-side `filter`).
- **Gaps**:
  - Dead code masking a missing feature: `DocScope = 'private' | 'team'` (`demo.ts:36`) has no DB column, no API field and no RLS policy.
  - Latent breakage: the moment `scope` becomes real, every document not authored by the literal string "James Okafor" disappears from every adviser's list.
  - Even if wired, filtering visibility in the browser after shipping the full payload is not a boundary — the rows are already on the wire.

## Document detail (`routes/documents/[id]/`)

### Preview / content serving

- **Status**: ✅
- **What it does today**: The load sets `previewUrl = /documents/{id}/content` when the API reports a `content_type` (`[id]/+page.server.ts:60`). That same-origin route (`[id]/content/+server.ts`) checks `documents:read`, calls `GET /documents/{uid}/content` (`documents.py:1501`), buffers the bytes (deliberately, to avoid Chromium's blank-PDF-on-chunked bug) and passes Content-Type/Disposition through. The API streams from private storage, audits `document.viewed`, and uses RFC-6266 dispositions (`documents.py:151`). Presigned URLs exist (`/{uid}/url`, `:1479`) but are correctly unused in-browser because the object store is compose-internal.
- **Expected**: As an adviser I open a document and see it without leaking a public URL.
  - PDFs/images inline; DOCX falls back to download.
  - No stable public object URL.
  - Each view audited.
  - A missing blob shows a clear state.
- **Gaps**: the sidebar "Download" button is `href={preview.src} target="_blank"` with no `download` attribute (`[id]/+page.svelte:231`), and the API returns `inline` for PDFs — so "Download" opens a tab rather than saving; the whole file is buffered twice (API → SvelteKit → browser) with no range support; a `queued` document yields `previewUrl = null` and the viewer's generic placeholder rather than "still rendering".

### Edit / save (rename + category)

- **Status**: ✅
- **What it does today**: `startEdit`/`saveEdit` (`[id]/+page.svelte:101-126`) post to the page's `rename` action (`[id]/+page.server.ts:71-86`) → `PATCH /documents/{uid}` (`documents.py:1542`), which COALESCEs name/type, audits `document.updated` with the changed keys, and 404s if the row isn't visible. The UI deserialises the result, surfaces failures via toast and `invalidateAll()`s.
- **Expected**: As an adviser I correct a document's name and category inline.
  - Both fields persist and are audited.
  - Failures are surfaced, not swallowed.
  - The category list matches the rest of the product.
- **Gaps**: `CATEGORIES` (`:95`) is a third hardcoded vocabulary, disjoint from the retype menu's; a document whose type came from a template (`suitability`) shows a Select whose options don't contain its own value; no validation on name length/emptiness client-side, and an empty name PATCHes `null` (a no-op) while the UI reports success.

### Archive (from detail)

- **Status**: ✅
- **What it does today**: `archiveDoc` (`[id]/+page.svelte:129-139`) posts the uid to the **list** route's archive action, checks `res.ok`, toasts and navigates to `/documents`. Backed by `POST /documents/{uid}/archive` (`documents.py:1576`), a soft `status='archived'` + `document.archived` audit.
- **Expected**: As an adviser I archive a document and can find or restore it later.
  - Soft archive; row stays queryable.
  - Confirmation before acting.
  - An archived view and an unarchive action.
- **Gaps**: no unarchive endpoint and no archived view anywhere in the platform — archiving is effectively a disappearance; the confirmation copy ("removed from the documents list") never says it is recoverable, because it currently isn't.

### Tag manager

- **Status**: 🎭 — confirmed: the backend does not exist.
- **What it does today**: The Tags tab renders `doc.tags`, which `toRow` hardcodes to `[]` (`lib/documents.ts:267`) because `DocumentOut` has no tags field. `tag-manager-dialog.svelte` keeps a local `current` array seeded on open (`:17-19`), has Add/Remove, **no Save**, only a Close, and prints "Tag editing is not wired up yet" (`:66`). Every edit is discarded on close. Server-side: `documents.document_tags` is mentioned only in a comment (`0005_documents_storage.sql:7`) and **was never created**; there is no `POST/DELETE /documents/{uid}/tags` endpoint despite `docs/document-management.md` listing them in the API table.
- **Expected**: As an adviser I tag documents and filter the library by tag.
  - Add/remove tags persisted per document.
  - Tag suggestions from existing tenant tags.
  - Filter and search the library by tag.
  - Tag changes audited.
- **Gaps**: no `document_tags` table, no migration, no endpoints, no persistence, no filtering — the entire feature is a dialog. The tab is reachable from every document and always reads "No tags."

### Versions tab

- **Status**: 🎭
- **What it does today**: `const versions = versionsByDoc[id] ?? []` (`[id]/+page.svelte:46`) reads a two-entry demo map keyed on `'doc-suit-v2'` (`demo.ts:181-196`). Real uids are `doc_<32 hex>`, so the lookup always misses and the tab always says "Only one version." There is no `GET /documents/{uid}/versions` endpoint (contrary to `docs/document-management.md`), and although `documents.superseded_by` exists (`0005_documents_storage.sql:51`) and the list query excludes `superseded`, **nothing in the codebase ever writes a supersede link** — upload, generate and the fulfil path all insert standalone rows.
- **Expected**: As an adviser I see a document's revision history and open any prior version.
  - Re-uploading/regenerating supersedes the prior row and records the link.
  - The tab lists every version, newest marked current.
  - Prior versions remain viewable and downloadable.
  - Version count visible on the list row.
- **Gaps**: no versions endpoint; no supersede write path anywhere; the tab is demo data keyed on uids that cannot occur; the list's "Version" column shows the _template_ version (`toRow`, `lib/documents.ts:262`), which is a different concept entirely.

### Requests panel (per-document)

- **Status**: ❌
- **What it does today**: `GET /documents/{uid}/requests` exists and is correct — it finds every request whose items target or were fulfilled by this document, and returns full headers plus items (`documents.py:1639-1678`). **No platform page calls it.** The detail page's four tabs are Details/Activity/Versions/Tags; `[id]/+page.server.ts` never fetches it.
- **Expected**: As an adviser, on a document I see who was asked to act on it and whether they have.
  - Pending/fulfilled/cancelled items listed with the client and due date.
  - Fulfilled items link to the document that satisfied them.
  - Raise a new request on this document from here.
  - Reassign a wrongly-attached agree/acknowledge item.
- **Gaps**: endpoint has zero consumers; the only trace of client action on a document is the raw audit verbs in the Activity tab (`document.agreed`, `document.acknowledged`) with no request context, no due date, no outstanding-item view.

## Requests engine (`documents.requests` + `request_items`)

### Create a request (staff)

- **Status**: ❌ (API ✅, platform UI absent)
- **What it does today**: `POST /documents/requests` (`documents.py:780-867`) is thorough: it rejects empty item lists, requires a target document for `agree`/`acknowledge`, validates the target contact is RLS-visible (`_assert_visible_contact`, `:176`), verifies every target uid resolves, then inserts the header plus items. Nothing in `apps/platform/src` calls it — a repo-wide grep for `documents/requests` in the platform finds exactly one hit, the dashboard's read-only `GET` (`routes/+page.server.ts:14`). The only writer is `app/flows/orchestrator.py:38` (`_ensure_request`), which creates one request per contact per onboarding execution with `requested_by = NULL`.
- **Expected**: As an adviser I ask a client for documents, agreements or a form without starting a journey.
  - Pick a client, add items (upload/agree/acknowledge/complete_form), set priority, due date, covering note.
  - Agree/acknowledge items pick a target document; complete_form items pick a builder form.
  - The client is notified and the request appears in their portal.
  - The request appears in a staff requests list with progress.
- **Gaps**: **no UI at all** — not on the documents list, not on the client detail page, not on the client Documents tab (`lib/components/clients/client-documents.svelte` offers only Send and Upload). Requests exist only as a side effect of starting a journey. No email on creation either.

### Client fulfilment path

- **Status**: ⚠️
- **What it does today**: `POST /documents/requests/{req}/items/{item}/fulfill` (`documents.py:944`) runs elevated on `firm_session`, gated by `r.contact_id = ANY(auth.contact_scope()) AND i.status='pending'`; for `upload` it sniffs and stores the blob, inserts the document, links the client as `owner` in `document_contacts`, audits a per-action verb, marks the item fulfilled, calls `flows_orchestrator.on_item_fulfilled` and rolls the parent up when nothing is pending. `complete_form` goes through `/form-response` (`:1108`) storing jsonb into `documents.form_responses`. Pre-consent review is properly supported via `/target` (presigned) and `/terms` (inline HTML) (`:1277`, `:1320`). Client-side, only `apps/client/src/routes/onboarding/+page.server.ts:126,154,178,198` consumes these.
- **Expected**: As a client I see everything my adviser has asked for and complete it in one place.
  - Every pending item across all requests is listed.
  - Each item is reviewable before confirming.
  - Fulfilment is atomic and rolls the parent up.
  - The adviser sees completion immediately.
- **Gaps**: the client's _only_ request surface is the onboarding page driven by flow **case steps** — a request created directly via the API (`requests.execution_id`/case link absent) is **invisible to the client**, so the standalone request path is unfulfillable end-to-end; no client-side requests inbox outside onboarding; `_FILE_ACTIONS = ("upload",)` means a `sign` action (still admitted by the DB CHECK per the docs) would fall into the confirm-only branch and self-fulfil.

### Request lifecycle (cancel / due date / expiry)

- **Status**: ⚠️
- **What it does today**: `POST /documents/requests/{uid}/cancel` cancels a pending header and its pending items (`documents.py:870`); `PATCH /documents/requests/{uid}` edits only the due date, 409-ing on non-pending requests (`:901`). Neither has a UI. Rollup to `fulfilled` happens inside both fulfilment endpoints. `due_date` is stored and displayed on the dashboard rail (`routes/+page.svelte:210-228`) but **nothing acts on it** — no expiry job, no overdue state, no reminder; the worker's scheduled scan (`worker.py:320+`) covers task assign/near-due emails only.
- **Expected**: As an adviser I manage an outstanding request's deadline and cancel it when it's moot.
  - Cancel from the UI, with confirmation; the client's item disappears.
  - Edit the due date; the client sees the change.
  - Overdue requests flagged and chased automatically.
  - Cancellation and edits audited.
- **Gaps**: no UI for cancel or due-date edit; no expiry/overdue derivation or reminder; request lifecycle changes are **not** written to `documents.audit_log` (only fulfilment verbs are), so a cancellation leaves no product-event trace; per-item cancel is impossible (only whole-request).

### Reassign a request item

- **Status**: ❌
- **What it does today**: `POST /documents/requests/{req}/items/{item}/reassign` (`documents.py:1359-1463`) is carefully built — re-points the target, re-opens an already-fulfilled item, re-opens the parent, re-links the contact, and audits both `document.agreement_superseded` (consent invalidated, kept as an append-only fact) and `document.reassigned`. No caller exists in either portal.
- **Expected**: As an adviser who attached the wrong document to an agreement, I can correct it and have the client re-consent.
  - Swap the target from the document or request view.
  - Prior consent is preserved in the trail and marked superseded.
  - The item returns to the client's pending list.
  - A reason is recorded.
- **Gaps**: zero UI. The most compliance-sensitive correction in the domain is API-only; `ACTIVITY_META` even ships labels for both verbs (`lib/documents.ts:194-200`) that can never be produced through the product.

### Staff requests view

- **Status**: ⚠️
- **What it does today**: The only staff-side view is the dashboard "Needs attention" rail: `GET /documents/requests` filtered to `status === 'pending'` (`routes/+page.server.ts:14-17`), showing the first item's title, the contact name and a due-date phrase, each linking to `/documents` (`routes/+page.svelte:210-228`).
- **Expected**: As an adviser I have one place to see every outstanding client ask and chase it.
  - List all requests with status, client, items, progress, due date.
  - Filter by status/client/overdue.
  - Drill into a request; cancel, edit, chase.
  - Fulfilled items link to what satisfied them.
- **Gaps**: capped at 5, shows only `items[0].title` (a 4-item request reads as one), and every row links to `/documents` which shows nothing about requests — a dead-end link; no requests list route, no request detail route, no per-client requests view.

## Files (`routes/files/` + `files` router)

### Files-vs-documents split

- **Status**: ✅
- **What it does today**: A document (`documents.documents`) is a financial document; a file (`documents.attachments`, migration `0018`) is a supporting client upload with an `attachment_type` and a direct `contact_id`. `GET /files` (`files.py:107`) is RLS-scoped and excludes soft-deleted rows. The `/files` page now `redirect(307, '/documents')` (`files/+page.server.ts:11`) while keeping its `upload`/`delete` actions live, and `fileToRow` (`lib/documents.ts:295`) folds files into the one library list with `isFile` switching row semantics (download/delete instead of view/share/archive).
- **Expected**: As an adviser I manage documents and supporting files in one place without confusing them.
  - One list, visually distinguished, filterable by kind.
  - File-appropriate actions only.
  - Files bind to a client at upload.
  - Old `/files` links keep working.
- **Gaps**: the merged row loses the file's real `attachment_type` nuance (`ATTACHMENT_TYPE_LABELS` becomes the `category`, so files and documents share a single "Type" column across two vocabularies); files are not shareable (`documents.shares` is polymorphic and ready, `resource_type='file'` is never created); no file detail page — a file's only interaction is download or delete.

### File upload

- **Status**: ✅
- **What it does today**: `/files?/upload` forwards the multipart body straight through (`files/+page.server.ts:15-28`) to `POST /files/upload` (`files.py:128`), which validates `attachment_type` against an allowlist, validates the contact is RLS-visible, sniffs magic bytes, stores the blob and inserts `documents.files` + `documents.attachments`.
- **Expected**: As an adviser I upload a client's passport and it is filed against them.
  - Type allowlist enforced server-side.
  - Optional client binding, validated.
  - Size/MIME limits enforced.
  - The upload appears in the library and on the client's record.
- **Gaps**: attachment uploads are **not audited** — `documents.audit_log` is document-keyed, so a passport upload leaves no product event (acknowledged as deferred in `docs/document-management.md`); the action omits the `locals.user` guard its siblings have (`files/+page.server.ts:15`), relying solely on `can(...)`; same single-file limitation as documents.

### File download

- **Status**: ✅
- **What it does today**: `/files/{uid}/download` (`files/[uid]/download/+server.ts`) checks `documents:read` and proxies `GET /files/{uid}/content` (`files.py:261`), buffering bytes and passing through Content-Type plus an RFC-6266 disposition (inline for PDF/PNG/JPEG, attachment otherwise). Whole-row click on a file row navigates there (`documents/+page.svelte:374`).
- **Expected**: As an adviser I download a supporting file safely.
  - Bytes served through an authorised same-origin proxy, never a public URL.
  - Correct filename, non-ASCII safe.
  - Non-previewable types download.
- **Gaps**: no download/view audit at all for files; a PDF "download" renders inline (same disposition issue as documents), so the row-click "download" behaviour is actually "open".

### File delete

- **Status**: ✅
- **What it does today**: Confirmed via `ConfirmDialog`, then `deleteFiles` → `/files?/delete` (`files/+page.server.ts:30-42`) → `DELETE /files/{uid}` (`files.py:291`), a soft `deleted_at` stamp that 404s if already deleted; the blob is retained.
- **Expected**: As an adviser I remove a file that shouldn't be there, recoverably.
  - Confirmation naming the file.
  - Soft delete; the blob retained for retention rules.
  - Restore available to an authorised role.
  - Deletion audited.
- **Gaps**: UI says "This cannot be undone" while the API soft-deletes and keeps the blob — misleading in both directions (a user believing it's gone, and a compliance reader believing it isn't); no restore endpoint or view; not audited.

## Shares

### Create a share link

- **Status**: ✅
- **What it does today**: `share-dialog.svelte` loads existing links on open and POSTs `{expires_in_days}` (`:44-86`) to the server-side proxy `documents/[id]/shares/+server.ts` (needed because a client component can't hold the access token) → `POST /documents/{uid}/shares` (`shares.py:104`), which RLS-checks the document, mints `secrets.token_urlsafe(24)`, inserts `documents.shares` with `expires_at = now() + days` and audits `share.created`. The URL returned points at the client portal's public page (`_share_url`, `shares.py:74`). Expiry is bounded 1–365 days (`ShareCreate`, `:32`).
- **Expected**: As an adviser I mint a time-limited public link to a document and copy it.
  - Unguessable token, bounded expiry, copied on creation.
  - Existing active link surfaced rather than duplicated.
  - Creation audited and visible in activity.
  - Access count visible.
- **Gaps**: nothing stops minting unlimited concurrent links for one document (the dialog shows only the first `active` one, `share-dialog.svelte:53`, so the others become invisible-but-live); no per-share covering message (the public page even comments on its absence); no password/email-gated option; documents only — files can't be shared.

### Token validation & expiry

- **Status**: ✅
- **What it does today**: `GET /share/{token}` (`shares.py:231`) is the sole unauthenticated path, runs under `system_transaction` (service_role), and matches on `token AND resource_type='document' AND revoked_at IS NULL AND expires_at > now()`, failing closed with one opaque 404 for unknown/revoked/expired alike. It increments `access_count`, stamps `last_accessed_at` and inserts an actor-less `share.accessed` audit row. `/share/{token}/content` (`:313`) re-validates the same predicate on every byte request.
- **Expected**: As a firm I want share links to be safe and to expire.
  - Token is the only credential; no id ever accepted.
  - Unknown/revoked/expired indistinguishable.
  - Every access counted and audited.
  - Bytes re-validated on each fetch, not just on resolve.
- **Gaps**: no rate limiting or throttling on the public endpoints (a token is 24 bytes so brute force is impractical, but access is unmetered); `access_count` increments on resolve only, so N byte-fetches after one resolve count as one access; no IP/user-agent captured on `share.accessed`.

### Revocation

- **Status**: ✅
- **What it does today**: The dialog's Revoke calls `DELETE /documents/{id}/shares?share_uid=…` (`share-dialog.svelte:88-103`) → the proxy's `DELETE` → `DELETE /shares/{share_uid}` (`shares.py:189`), an idempotent `revoked_at`/`revoked_by` stamp that audits `share.revoked` and distinguishes unknown (404) from already-revoked (ok).
- **Expected**: As an adviser I kill a link immediately and prove when I did.
  - Revoke takes effect on the next access.
  - Idempotent; already-revoked is not an error.
  - Revocation audited with actor and time.
  - Revoked links listed historically.
- **Gaps**: the dialog only ever exposes the _first active_ link, so any other live link for the same document cannot be revoked from the UI; no history view of expired/revoked links despite `list_document_shares` returning them all (`shares.py:163`); revoking does not invalidate an already-issued presigned object URL (up to 15 minutes of residual access).

### Public share page

- **Status**: ✅
- **What it does today**: `apps/client/src/routes/share/[token]/+page.server.ts` is unauthenticated, resolves the token via the API, renders "link unavailable" on any non-ok, and computes days-left once server-side to avoid hydration drift. Bytes come through the public same-origin proxy `share/[token]/content/+server.ts`, with `?download=1` forcing a save.
- **Expected**: As a recipient without an account I open a link and read or save the document.
  - No login required; branded with the sending firm.
  - Inline viewer for PDFs/images, download for the rest.
  - Clear expiry and a clear expired/revoked state.
- **Gaps**: the type label map (`:7-14`) is a fourth copy of the document-type vocabulary and mislabels categories like `report`/`tax`/`agreement` as the generic "Document"; no covering message (none is collected); no recipient identification, so `share.accessed` can never say _who_ opened it.

## Forms responses

### Staff review of client-submitted answers

- **Status**: ⚠️
- **What it does today**: A per-form Responses tab (`routes/forms/[id]/+page.svelte:308-340`, loaded at `forms/[id]/+page.server.ts:27-39`) reads `GET /forms/{uid}/responses` (`forms.py:513`), which selects from `documents.form_responses` **by `form_uid`** — deliberately catching request-driven client fills (which carry no `form_id`) as well as staff standalone submissions — and tags each row `request` or `standalone`. Answers render as a label/value list. The write side is `POST /documents/requests/{req}/items/{item}/form-response` (`documents.py:1108`). Journeys detail (`routes/journeys/[uid]/`) does **not** show submitted answers.
- **Expected**: As a staff reviewer I see every client form submission, know who submitted it, and work through them.
  - A submissions inbox across all forms, newest first, with unreviewed state.
  - Each submission attributed to the client (contact), not just the login.
  - Reviewed/accepted/rejected state with a reviewer and timestamp.
  - Reachable from the client record and from the journey that requested it.
- **Gaps**:
  - **No submissions inbox.** Review requires knowing which form to open — there is no cross-form or cross-client queue, and no unreviewed count anywhere in the shell.
  - **No client attribution.** `form_responses.contact_id` is stored (`documents.py:1158`) but `list_form_responses` never selects it; the tab shows `submitted_by` joined to `auth.users`, so a client fill displays the login's name with no contact link.
  - No review state at all — nothing marks a submission read, accepted or rejected; no reviewer, no note.
  - Not surfaced on the client record or the journey detail, so the answers a journey step collected are only findable by navigating to the form.
  - Answers are a raw jsonb dump; `answerLabel`/`answerValue` resolve against the _live_ form fields, so a later field rename/removal silently changes or blanks how a historical submission reads.

---

## DOMAIN SUMMARY

| #   | Function                         | Status | One-line verdict                                                                   |
| --- | -------------------------------- | ------ | ---------------------------------------------------------------------------------- |
| 1   | Documents library table          | ⚠️     | Real, merged list; `loadError` computed then dropped → failures look empty         |
| 2   | Filters / search / sort / paging | ✅     | Client-side over full payload; no URL state, no facets                             |
| 3   | Type drill-in ("folders")        | ⚠️     | A client-side filter on a flat string, not a hierarchy                             |
| 4   | Drag-to-move / Change type       | ⚠️     | Persists via PATCH but writes a **different taxonomy** than upload/edit            |
| 5   | Upload flow                      | ⚠️     | Persists correctly; multi-file drops lose files; "Auto-classify" = `other`         |
| 6   | Generate flow                    | ✅     | Real async pipeline (render_jobs → worker); worker-down is invisible               |
| 7   | Send flow                        | ⚠️     | Binds `document_contacts`; **message discarded, no email despite the promise**     |
| 8   | Bulk archive / delete            | ✅     | Confirmed, response-checked, N+1 requests                                          |
| 9   | Per-row kebab actions            | ⚠️     | Share label from demo data; "Resend" doesn't prefill; no permission gating         |
| 10  | Activity rail                    | ✅     | Live `documents.audit_log`, grouped + filterable; hard 100-event cap               |
| 11  | `CURRENT_USER` filter            | 🎭     | Dead code (all rows are `scope:'team'`); latent list-wipe                          |
| 12  | Detail preview / content         | ✅     | Authorised same-origin proxy; "Download" actually opens inline                     |
| 13  | Detail edit / save               | ✅     | Persists + audits; third disjoint category vocabulary                              |
| 14  | Detail archive                   | ✅     | Soft archive; no archived view, no unarchive                                       |
| 15  | Tag manager                      | 🎭     | No table, no endpoint, no Save — confirmed backend absence                         |
| 16  | Versions tab                     | 🎭     | Demo map on impossible uids; no endpoint; supersede never written                  |
| 17  | Per-document requests panel      | ❌     | Endpoint complete, zero consumers                                                  |
| 18  | Create request (staff)           | ❌     | API complete; no platform UI — only journeys create requests                       |
| 19  | Client fulfilment path           | ⚠️     | Solid API; client surface is onboarding-only, so ad-hoc requests are unfulfillable |
| 20  | Request lifecycle                | ⚠️     | Cancel + due-date PATCH exist with no UI; no expiry/overdue/reminders              |
| 21  | Reassign request item            | ❌     | Compliance-critical correction is API-only                                         |
| 22  | Staff requests view              | ⚠️     | 5-item dashboard rail linking to a page that shows nothing about requests          |
| 23  | Files-vs-documents split         | ✅     | Clean separation, merged presentation; files not shareable                         |
| 24  | File upload                      | ✅     | Validated + bound to contact; unaudited                                            |
| 25  | File download                    | ✅     | Authorised proxy; unaudited; inline-not-download                                   |
| 26  | File delete                      | ✅     | Soft delete; UI claims irreversible; no restore                                    |
| 27  | Create share link                | ✅     | Real token + bounded expiry + audit                                                |
| 28  | Token validation / expiry        | ✅     | Fails closed, opaque 404, re-validated per byte fetch                              |
| 29  | Share revocation                 | ✅     | Idempotent + audited; only the first active link is reachable                      |
| 30  | Public share page                | ✅     | Unauthenticated, branded, proxied bytes                                            |
| 31  | Forms responses review           | ⚠️     | Per-form tab is real; no inbox, no contact attribution, no review state            |

## Top 5 severest gaps

1. **The requests engine has no staff UI — and standalone requests are unfulfillable.** `POST /documents/requests` (`documents.py:780`), `cancel` (`:870`), `PATCH due_date` (`:901`), `reassign` (`:1359`) and `GET /documents/{uid}/requests` (`:1639`) are all built and tested; not one has a caller in `apps/platform/src`. The only writer is `flows/orchestrator.py:38`. Worse, the client's only fulfilment surface reads flow **case steps**, so even a request created directly via the API would never appear to the client. "Ask a client for a document" — the domain's headline verb — does not exist as a product feature.

2. **Send promises an email it never sends, and silently discards the covering message.** `send-dialog.svelte:144` states "Recipients will be notified by email"; `documents.py:486` writes `document_contacts` and audit rows only, and `SendDocument.message` (`:478`) is parsed and never used. There is no document-send path in `app/email/transport.py`, `app/tasks/notifier.py` or `app/flows/notifications.py`. Clients are only told about documents if they happen to log in, and advisers believe otherwise.

3. **Three (four, counting the public page) disjoint document-type vocabularies, one unvalidated column.** Retype offers `illustration/suitability/valuation/review/factfind/statement` (`packages/ui/src/lib/templates/types.ts:34`), upload offers `report/statement/agreement/identity/tax/other` (`upload-dialog.svelte:36`), the detail editor repeats the latter (`[id]/+page.svelte:95`), the share page maps a fifth set. `document_type` is free text with no CHECK (`0005_documents_storage.sql:53`). Drag-to-move therefore writes values the rest of the product cannot represent, and category browse shows raw DB strings.

4. **Tags and versions are shipped, reachable UI over absent backends.** `document_tags` was never created (only named in a comment at `0005_documents_storage.sql:7`); there are no tag endpoints and the dialog admits it (`tag-manager-dialog.svelte:66`). `versionsByDoc` (`demo.ts:181`) is keyed on demo uids so the Versions tab is permanently "Only one version", there is no versions endpoint, and `superseded_by` is never written by any code path. Two of four tabs on every document detail page are non-functional.

5. **Silent failures on the library's read path and the upload path.** `loadError` is computed with an explicit "surfaces as a retry banner" comment (`documents/+page.server.ts:36-38`) and never rendered — a token-refresh 401 or an API outage presents as "No documents match your filters". Simultaneously, a multi-file drop posts only `files[0]` (`upload-dialog.svelte:71`) and reports success, so files the user watched land in the dropzone are discarded without a word.
