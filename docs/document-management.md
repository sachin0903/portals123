# Document management

One doc for the base module: this **overview** (schema · build order · status) up top, the **full
build spec** below. Phase-2 generation is separate → `document-generation.md`.

Owner **PD2**. The portal's first domain module. Built on Postgres RLS + SQL-first migrations
(`0001_auth_core`), and **reconciled to the canonical `documents.*` in `proposed-schema.sql`** — the
few divergences below are the minimum the upload/management base needs, each flagged to fold back
into `proposed-schema.sql`.

## Schema — `documents.*`

Keys follow canonical conventions: **`files` and `documents` are `uuid`** (with `documents.uid text`
for API paths); the base-module tables are `int` + `uid`. `tenant_id int` → `auth.tenants`; **actor**
refs (`generated_by` / `added_by` / `requested_by` / `submitted_by`) are `uuid` → `auth.users`; the
**client** link (`document_contacts.contact_id` / `requests.contact_id` / `document_tasks.contact_id`
/ `form_responses.contact_id`) is `int` → `crm.contacts` (bound in `0015`); `account_id` /
`application_id` are `int` (→ `crm` / `platform`). Client isolation is **RLS, not app code** — a
client has an empty `auth.tenant_lineage()`, so they reach a document only through the `contact_scope`
branch. Storage
keys are **path-addressed**. Full DDL: the Appendix.

| Table                        | Purpose                                                                                                               | vs canonical                                                    |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `files`                      | Blob metadata — `bucket` · `storage_key` · `content_type` · `size_bytes` · `sha256`                                   | ✅ canonical                                                    |
| `documents`                  | The record — uploaded **or** generated (`status`, `source`, `account_id`/`application_id`, `superseded_by`)           | ✅ + `source`, nullable `template_version_id`, `name` (flagged) |
| `document_contacts`          | Links a doc to a client contact — **drives client visibility**                                                        | ✅ canonical                                                    |
| `audit_log`                  | Product events (`pdf_viewed` · `shared` · `task_*`), append-only                                                      | ✅ canonical                                                    |
| `document_tasks`             | Client action items — `review` · `acknowledge` · `upload_requested`                                                   | ➕ base-module (propose to canonical)                           |
| `document_tags`              | `period` · `category` · `free` tags                                                                                   | ➕ base-module                                                  |
| `requests` + `request_items` | Adviser asks a client to upload / agree / acknowledge / complete a form (header + independently-fulfilled line items) | ➕ base-module (replaced the upload-only `upload_requests`)     |

**Sharing → `documents.shares`** (a documents-local, polymorphic share primitive; the planned
`crm.shares` was not used — see [Sharing](#sharing-documentsshares)). The generation tables (`templates`, `template_versions`,
`datasets`, `document_runs`, `schedules`) are canonical too but belong to **Phase 2**.

Enums (canonical): `document_status` = `queued · generating · generated · failed · superseded ·
archived` — **no `pending_action`**; "action needed" is derived from open `document_tasks`. Free
row-level DML audit comes from `0001`'s `audit.attach`.

## Build order (sequence, not dates)

1. **Storage foundation** — object store (MinIO **or** RustFS) in `docker-compose.local.yaml`; a
   private-document track in `storage.py` (private ACL · magic-byte sniff · presign · proxied
   stream); a MinIO test fixture. **No CRM dependency — start here.**
2. **Base schema + staff view** — the `documents.*` migration (schema + RLS + audit); routers
   `list · upload · get · pdf · url · versions`; the platform list + viewer.
3. **Tasks · sharing · client view** — tasks, sharing via `documents.shares` + the public resolve
   (sharing has since been REMOVED — see below),
   upload-requests; the Sent → Viewed → Accepted lifecycle; the client card-list.
4. **Admin + classify** — admin oversight; the firm classifies uploads (never the client).
5. **Templates (persist only)** — the canonical `templates` / `template_versions` / `datasets`
   tables persisted; **no rendering**.
6. **Generation** — the Jinja2/WeasyPrint render worker (+ `document_runs` / `schedules`) →
   **Phase 2** (`document-generation.md`).

Steps 1–2 are staff-only and unblocked. Step 3's **client** view and sharing need `0002` (below).

## Status & decisions

- **✅ Built so far (steps 1–3, staff **and** client):** `0005_documents_storage` (`files` ·
  `documents` · `audit_log` + staff RLS), `0006_document_collab` (`document_contacts` ·
  `document_tasks` + the client-branch RLS on `documents`/`files`), `0008_document_requests_v2`
  (`requests` header + `request_items` lines — supersedes the upload-only `0007_document_requests`,
  which it drops), `0010_form_responses` (a `form_uid` on `request_items` + a
  `documents.form_responses` table for completed forms), and `0015_documents_crm_contacts` (reconciles
  the client links onto `crm.contacts` — see below). Routers: `list · upload · get · url · content ·
archive · rename (PATCH) · activity (per-doc + tenant feed) · tasks (GET/POST/PATCH) · clients · send ·
requests (GET/POST · update-due (PATCH) · cancel · items/{id}/fulfil · items/{id}/form-response)`. Both portals are wired to the API — platform
  list/detail/upload/archive/rename/tasks/**send**/**multi-item request**/**live recent-activity feed**,
  client list/detail/complete-task/**per-item fulfil**. The overview feed reads `documents.audit_log`
  via `GET /documents/activity` (RLS staff-scoped, ordered `created_at DESC, id DESC` for a stable
  newest-first). Fulfilment audits a **per-action verb** (`document.agreed` / `.completed` /
  `.acknowledged`, not a blanket `.uploaded`) so the feed reflects what happened; the platform maps
  each verb to a label/icon via `ACTIVITY_META`.
- **Requests are multi-item, multi-action.** A `documents.requests` header (client · priority ·
  due date · message) carries one or more `documents.request_items`, each with its own **action**
  (`upload` · `agree` · `acknowledge` · `complete_form`), title, category, format hint, and optional
  `target_document_id` (the doc to agree to / acknowledge). Items are fulfilled independently; the
  parent flips to `fulfilled` once none remain pending. **File-backed** actions (upload, complete_form)
  create a document; **confirm-only** actions (agree, acknowledge) act on the target doc — `agree`
  shows the client the document with an "I agree" checkbox (real signing is a later addition), and
  `acknowledge` is a one-tap confirm. The app layer uses `agree` (not the legacy `sign` value the
  `request_items.action` CHECK still admits); the API only ever creates
  `upload`/`agree`/`acknowledge`/`complete_form` items. **The target document a client reviews is
  served to them SAME-ORIGIN** — `/terms` for a declaration authored as inline HTML, and `/target`
  (name + content type) plus `/target/content` (the bytes) for one carrying a file, proxied by the
  portal at `/api/request-items/{req}/{item}/content`. `/target` returned a presigned object-store
  URL until Sept 2026, which is why a PDF declaration opened a frame that never filled: the store
  has no public endpoint in a container deployment, and the portal draws a PDF with pdf.js, which
  fetches the bytes rather than navigating to them, so a cross-origin URL needs CORS the store does
  not send. A `document_type='terms'` row may hold a file and no HTML, so the fall-through between
  the two is `content`, never `/terms`' status code.
  `request_items` reads reach the client via a one-way subquery into `requests` (no recursion —
  `requests` policies never reference the items). A `complete_form` item carries a **`form_uid`** (the
  builder form to render); on submit the collected answers are stored as a jsonb blob in
  `documents.form_responses` (keyed by fieldKey — the `DynamicForm.collect()` shape) and the item flips
  to `fulfilled` (no document is created). `form_responses.contact_id` is **nullable**: a
  request-driven client fill carries the client's contact, but a standalone staff form submission (via
  the forms router) has no client contact, so it's `NULL` (`ON DELETE SET NULL` keeps the record if the
  contact is later removed). A request's **due date is editable** via
  `PATCH /documents/requests/{uid}` (staff, RLS-scoped).
- **Dev seeding.** `scripts/seed_documents.py [alias]` uploads sample PDFs (storage-backed);
  `scripts/seed_clients.py [alias]` upserts a handful of enabled client-portal users so the Send /
  Request / Assign-task pickers have real people. Both default to the `graphene` tenant, are
  idempotent, and run as `service_role`. Run with `PYTHONPATH=. uv run --directory apps/api python
scripts/…` (the `app` package must be importable).
- **✅ Bound to the true client model `crm.contacts`.** The collab tables (`document_contacts`,
  `document_tasks`, `requests`, `form_responses`) bind the client by `contact_id int → crm.contacts`
  (the canonical client entity), and client-visibility RLS scopes through the resolved `contact_ids`
  claim → `auth.contact_scope()`, **not** `auth.uid()`. `0006`/`0008`/`0010` shipped an interim
  `user_id uuid → auth.users` stand-in while `0002` was outstanding; **`0015_documents_crm_contacts`**
  reconciled every link onto `crm.contacts` (`document_contacts` PK `(document_id, contact_id, role)`;
  `requests.contact_id`; `form_responses.contact_id`). `requested_by` (the staff member who raised a
  request) and `submitted_by`/`generated_by`/`added_by` (the acting staffer) stay `uuid → auth.users`
  — they are actors, not clients. `document_contacts` and `request_items` also **denormalise
  `tenant_id`** to keep their RLS policies off a recursing subquery.
- **The client fulfils a request item via an elevated endpoint.** A client has empty
  `auth.tenant_lineage()` and can't write `documents` under RLS, so
  `POST /documents/requests/{req_uid}/items/{item_uid}/fulfill` runs on `firm_session`
  (`service_role`, BYPASSRLS): it validates the parent request is the caller's
  (`r.contact_id = ANY(auth.contact_scope())`) and the item is `pending` (that ownership check is the
  entire authz boundary), then — for file-backed actions — stores the blob, inserts the doc, links the
  client via `document_contacts` (its `contact_id`); marks the item fulfilled and rolls the parent up.
  Delivery to a client (Send, or assign-task) links them via `document_contacts` so the doc appears in
  their portal.
- **`crm.contacts` is live and drives client visibility.** `resolve_claims` populates the
  `contact_ids` claim (a login maps to its parties via `crm.contact_users`), and step 3's client
  visibility scopes through `document_contacts.contact_id`/`requests.contact_id` against
  `auth.contact_scope()`. The `account_id`/`application_id` FKs and `crm.shares` land with the wider
  `crm`/`platform` schema.
- **RustFS** — an S3-compatible object store, a drop-in for MinIO behind `storage.py`.
- **Reconciled to `proposed-schema.sql`:** `uuid` PKs for `files`/`documents`; sharing via
  `crm.shares` (dropped `documents.document_shares`/`_accesses`); typed `account_id`/`application_id`
  binding (dropped the polymorphic `subject_ref`); `document_status` matches canonical (dropped
  `pending_action`); removed the dead `documents.events` clone.
- **Flagged extensions to fold into `proposed-schema.sql`:** `documents.documents.source` + nullable
  `template_version_id` + `name` (to admit uploaded docs); `document_tasks` / `document_tags` /
  `requests` + `request_items`; `crm.shares.resource_uid text` (so a `uuid` document is shareable); a document
  reference on `events.events` (so document domain events fit the bus); optionally
  `audit.log.entity_uid text` (so the DML spine can index `uuid` rows — deferred). See
  [Audit & events](#audit--events).
- **Open decisions:** adviser share/archive scope · non-PDF viewing · task-completion hardening ·
  RustFS-vs-MinIO · SSE prod flag · `0002` timing. **Decided:** no admin template editor; `uuid` PKs
  (match canonical); "action needed" derived from open tasks (no `pending_action`); sharing via
  `crm.shares`.

---

# Build reference

## Foundation (from `0001_auth_core`)

- Tenant key is `int` (`auth.tenants.id`); person refs are `uuid` (`auth.users.id`); `contact_id` /
  `account_id` / `application_id` are `int` (→ `crm`/`platform` in `0002`). **`files` and `documents`
  PKs are `uuid`** (canonical); the base-module tables are `int` + `uid`.
- RLS is **claim-driven**: `tenant_session` does `SET LOCAL ROLE authenticated` + publishes
  `request.jwt.claims`. Policies use `auth.tenant_lineage()` · `auth.contact_scope()` ·
  `auth.account_scope()` · `auth.can_read_all()` · `auth.is_system()`. Staff predicate:
  `tenant_id = ANY(auth.tenant_lineage())`; a client has empty lineage → `contact_scope` only.
- Roles: `authenticated` (request), `service_role` (BYPASSRLS — worker + the share fn), `migrator`.
- **Audit & events** — three separate mechanisms, don't conflate them; see the subsection below.

## Audit & events

Three separate mechanisms — they map to the two admin screens (audit log · events feed) plus the
compliance spine. Don't conflate them.

| Mechanism                   | Role                                                                                                                                  | Written by                        | Status                                                                                    |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------- | ----------------------------------------------------------------------------------------- |
| `audit.log` (0001 spine)    | Row-level **DML evidence** — "row X mutated by Y" (compliance); powers the admin **audit-log** screen                                 | trigger (`audit.attach`)          | ✅ built                                                                                  |
| `events.events` (canonical) | Internal **domain-event bus / outbox** — "`document.shared` happened" → drives notifications, integrations, the **events** feed       | the app (documents **emit** here) | ⚠️ table not built — the built `app/events.py` is the WorkOS _consumer_ of the same shape |
| `documents.audit_log`       | Per-document **activity feed** — the non-DML verbs (`pdf_viewed` · `downloaded` · `shared`) the trigger can't see; powers `/activity` | the app                           | ships with this module                                                                    |

- **Documents emit domain events** into `events.events` (first-class): `document.uploaded` ·
  `document.shared` · `document.generated` · `document.signed`. That's the fan-out for client emails
  / in-app notifications — the same shape as the WorkOS `session.created` stream, but internal.
- Keep the three **disjoint**: a DML change → spine; a verb for the doc's own timeline →
  `documents.audit_log`; a semantic event other systems react to → `events.events`. Never write the
  same fact to two.
- **Additive needs (fold into `proposed-schema.sql`):** `events.events` binds only via
  `application_id int` today — add a document reference (`resource_type`/`resource_id`, or a typed
  `document_id`) so document events fit. And `audit.record()` casts a row's `id` to `bigint`, so
  `uuid` document rows log `entity_id = NULL` in the spine (row still in `detail`) → a later
  `audit.log.entity_uid text` restores indexed lookup.

## Portals & roles

| Portal           | Audience     | Surface                                                                          |
| ---------------- | ------------ | -------------------------------------------------------------------------------- |
| platform `:5175` | staff        | the hub — list/stats/filters, upload, viewer, tag/task/version/archive, requests |
| client `:5173`   | clients      | own docs only (task or own upload); take-action; fulfil requests                 |
| admin `:5174`    | Graphene ops | cross-tenant read/oversight (lower MVP priority)                                 |

Capability (app-side, layered on RLS): **admin/system** = full within tenant · **adviser** =
view/upload/update/tag/assign + share/archive own-or-assigned · **client** = view shared/tasked docs,
upload when requested, complete tasks. Row boundary is RLS, not `WHERE`.

Graphene Operations mounts the same library, detail, request, generation, send and document-type
surfaces under `/tenants/{uid}/operate/documents`. Every API request carries `tenant=<uid>`;
read-only mounts omit upload, edit, archive, send and request actions, and every server action
requires `tenant_portal:write` before forwarding. The target session then supplies the platform
document permission vocabulary so the unchanged domain checks and RLS rules run against the
selected firm. Personal notification preferences and storage credentials are not proxied.

## Storage

- S3-compatible (MinIO/RustFS local · S3/R2 prod), **private** bucket. Keys **path-addressed**:
  `uploads/{tenantId}/{docUid}/original.{ext}` · `documents/{tenantId}/{docUid}/v{n}.{pdf|html}`.
- `sha256` recorded for integrity (no dedup v1). Bytes reach the browser only via a short signed URL
  or the proxied stream — no stable public object URL.
- `storage.py` additions: private-doc `put` (forced-private ACL · magic-byte sniff · allowlist
  PDF/JPEG/PNG/DOCX ≤ 25 MB), `presigned_get` (15-min), proxied `/pdf`\|`/content` stream.

## API (uid-based; all under `tenant_session` except `/share`)

| Method             | Path                                                                 | Who                      | Notes                                                                                           |
| ------------------ | -------------------------------------------------------------------- | ------------------------ | ----------------------------------------------------------------------------------------------- |
| GET                | `/documents`                                                         | all (role-filtered)      | list; excludes `superseded`; `q`/`type`/`author`/`client` narrow it (see Filters below)         |
| POST               | `/documents/upload`                                                  | staff                    | multipart; auto-classify · sha256 · supersede · audit                                           |
| GET · PATCH        | `/documents/{uid}`                                                   | all · staff              | metadata · update name/type                                                                     |
| POST               | `/documents/{uid}/archive`                                           | staff (write)            | soft archive; **409** when `deletable = false`                                                  |
| GET                | `/documents/{uid}/pdf` · `/content` · `/url`                         | all                      | proxied PDF · images · 15-min signed download                                                   |
| GET                | `/documents/{uid}/versions` · `/activity`                            | all · staff              | version CTE · audit log                                                                         |
| GET · POST         | `/documents/{uid}/tasks`                                             | staff                    | list · assign (creates a task)                                                                  |
| PATCH              | `/documents/{uid}/tasks/{tid}`                                       | client / staff           | complete · cancel (recomputes action-needed)                                                    |
| GET · POST · PATCH | `/documents/requests[/{uid}]`                                        | staff (client reads own) | list · create (multi-item) · update due date; `POST /{uid}/cancel`                              |
| POST               | `/documents/requests/{req}/items/{item}/fulfill` · `/form-response`  | client (owns)            | upload a file (file-backed items) · submit a completed form → `documents.form_responses`        |
| GET · POST         | `/documents/requests/{req}/items/{item}/form` · `/reassign`          | client (owns) · staff    | fetch the item's form to fill · re-point an agree/acknowledge item at the correct document      |
| GET                | `/documents/requests/{req}/items/{item}/terms` · `/target[/content]` | client (owns)            | what an agree/acknowledge item asks them to read: inline HTML · name + content type · the bytes |
| POST · DELETE      | `/documents/{uid}/tags[/{tag}]`                                      | staff                    | add · remove                                                                                    |
| GET                | `/documents/clients`                                                 | staff                    | picker — the tenant's client-portal users                                                       |
| GET                | `/documents/{uid}/requests`                                          | staff (client reads own) | requests touching this document (targeted or fulfilled by it)                                   |

## Rules

- **Client-facing release is its own permission (`documents:send`, split out of
  `documents:write` in migration `0042`).** Send, request create and request-item reassign gate
  on it; upload/generate/rename/archive stay `documents:write` (drafting). Likewise the response
  plane left `forms:*` authoring: recording a standalone submission is `forms:submit`, and the
  response LISTING is `forms:submit` or `documents:read` — never bare `forms:read`, so a
  form-definition viewer cannot read client submissions. See `docs/api.md` → Permissions.
- **The firm-wide library is its own permission (`documents:show`, split out of
  `documents:read`).** The sidebar link and `/documents` need it on top of `documents:read`;
  without it a role reads documents only on a client's record. Portal-layer only — the API does
  not check it. Off by default: no role is granted it until a firm ticks it in the role editor.
- Archive is soft (`status=archived`; rows stay queryable).
- **A document can be marked undeletable** — `documents.documents.deletable` (`0122`), default
  `true`. It is for a record the firm does not own: one synced from a custodian is that
  custodian's, and a firm archiving it would diverge our copy of the book from theirs. Enforced
  in the API (`archive` answers 409 and names the reason), deliberately **not** by RLS — a policy
  that hid the row from the UPDATE would answer "not found" for a document the reader can see
  listed. The UI withholds Delete in both kebabs, excludes the row from the bulk bar's count
  (the count _is_ the delete target), and the record states why — the last only to a reader who
  would otherwise have the button, since their own permissions are the reason otherwise.
  **It is deliberately NOT an upload form field.** Nothing sets it back to true (no PATCH
  field, no endpoint), so a caller who could post `deletable=false` could mint a document only
  a hand-written SQL UPDATE can remove. Provider ingest does not upload over HTTP — it writes
  through `_store_upload`'s keyword, where the decision belongs to code rather than to whoever
  can reach the endpoint. Pinned on the signature by `test_documents_deletable.py`.
- **A document's type is set at upload and is not changed afterwards.** The library's kebab
  submenu, the drag-onto-a-type-chip gesture, the client tab's submenu and the record's category
  picker are all gone, as is the `?/retype` form action and the `typeChangeBlocked` guard that
  existed only to police them. `PATCH /documents/{uid}` still accepts `document_type` (the upload
  path sets one); no UI posts it.
- **The record has two tabs, Details and Events.** The Requests tab — which listed the requests
  touching a document and offered "Raise a request" — is gone: a request asks a client for
  something that does not exist yet, so raising one from the record of a document that already
  exists was the wrong door. `GET /documents/{uid}/requests` is kept; nothing calls it.
- Supersede via `supersedes_uid`; prior doc hidden from the list, kept in version history.
- **Action needed is derived, not a status.** A doc with ≥1 open `document_tasks` row shows _action
  needed_; completing or cancelling the last open task clears it. No `pending_action` enum value —
  this avoids the reference's cancel-stranding bug by construction.
- **Firm classifies uploads — never the client.** A client sees a doc only via a task or own upload.
- **The library's filters are server-driven** (`app/listing.py`, the `crm.py::list_contacts`
  shape): `q` searches the NAME, and `type` / `author` / `client` narrow the whole book before
  the page is cut, so `total` counts matches and facets describe the match set. **The client is
  filtered by UID, never by display name** — two clients legitimately share a name, and the
  estate addresses a client by uid everywhere else. `GET /files` takes the same four, because
  the library is ONE list built from both endpoints: a filter that reached only `/documents`
  would silently leave every supporting file in the table. Facets are unioned and totals summed
  in the loader; paging stays client-side over the merged answer, since a merged `total` cannot
  drive one pager — so when the match set is larger than the fetch the counter SAYS the list is
  cut ("showing the first 200 — narrow the filters to reach the rest") rather than quoting a
  total the pager cannot walk to. The loader also pins `offset: 0`: this pager never writes
  one, and a stale `?offset=` would silently drop that many rows off the front of both halves.
  (Folding `documents.attachments` into `GET /documents` behind a `?kind=document|file|all`
  would make paging honest too — a later change.)
- **The client facet's values are uids, so the API returns their NAMES beside them**
  (`facet_labels`, on `ListingPage`). They must be built the way the options are: `facets_for`
  computes the client options with the client filter LIFTED, so naming them from the rows on
  screen resolves only the client already filtered to and renders every other option as a bare
  nanoid. Both listings share `listing.apply` for the search/filter/facet/cut sequence, because
  the invariant that a filter narrows both halves is one a copy cannot hold.
- Uploads PDF/JPEG/PNG/DOCX ≤ 25 MB, magic-byte sniffed, served `Content-Disposition: attachment`;
  DOCX is download-only.
- Share tokens: 7-day, 24-byte base64url, every access logged (in `crm.share_accesses`).

## Sharing (`documents.shares`) — **REMOVED**

**Time-limited public share links for documents were removed** — the whole feature: the three
authenticated endpoints (`app/routers/shares.py`), the two unauthenticated ones (`GET /share/{token}`
and its byte proxy), the client portal's `/share/[token]` landing page, the platform's Share dialog
and kebab item, the `share` rate-limit surface, and the `document_share` email code.

Three things deliberately stayed, and why:

- **The table.** `documents.shares` and migration `0017_document_shares` are untouched. Nothing reads
  or writes them, but `documents.audit_log` holds `share.created` / `share.accessed` / `share.revoked`
  rows whose `detail` names a `share_uid`, so dropping the table would strand that evidence. Its RLS
  policies stand with it, as do the tests that pin them (`test_schema_invariants.py`,
  `test_rls_book_scope.py`). Dropping it is a separate, deliberate contract-phase migration.
- **The audit READ vocabulary.** `action.shares-*` rows are in `audit.actions` for good. `describe.ts`
  still humanises them ("Created a document share" / "Accessed a document share"),
  `audit_stream.py`'s `document` and `document_family` filters still include the `action.shares-%`
  prefix, and `admin._UID_KIND_BY_TAG['shares']` still resolves their `{uid}` to a document name.
  Removing any of those would silently degrade historic audit history — a hard no here — so each site
  carries a comment saying so. The PRODUCERS are gone (`actions.SHARE_ACCESS_EVENT`,
  `ANONYMOUS_SHARE_SUB`, and the event-schema registry's capture entry), so nothing appends a new row.
- **A firm's published `document_share` email templates.** The code was RETIRED from
  `email_catalog.KNOWN_CODES`, not deleted from anyone's database: an existing row stays listed,
  readable and editable, but the code is no longer offerable on either catalogue endpoint and no send
  site resolves it, so nothing renders it. No migration touches firm content.

## Files vs documents (`documents.attachments`)

**Built (migration `0018_attachments`).** Files are a **first-class entity, not a document type.**

- A **document** (`documents.documents`) is a financial document — generated or uploaded
  (suitability report, illustration, statement). A **file** (`documents.attachments`) is a
  supporting client upload — passport, national ID, proof of address. Separate table, separate
  router (`app/routers/files.py`, `/files`), separate lifecycle: files are **upload-only** (never
  generated) and carry an `attachment_type`.
- Both share the blob layer (`documents.files`) and bind to the true client model: an attachment
  has a direct `contact_id → crm.contacts`, and the client sees their own files through
  `auth.contact_scope()` (staff by tenant lineage) — the same RLS shape as documents.
- Endpoints (gated by the existing `documents:read` / `documents:write` — same people manage both):
  `POST /files/upload` (classify + optional client bind), `GET /files` (`?contact_id` to scope),
  `GET /files/{uid}`, `GET /files/{uid}/url` (presigned), `DELETE /files/{uid}` (soft delete).
- **No back-migration**: existing uploaded documents stay documents; files accrue going forward.
- UI: a platform **Files** page (list · upload · download · delete) beside Documents, and a
  read-only **Files** surface in the client portal. Tests: `apps/api/tests/test_files_api.py`.
- Deferred: auditing attachment uploads/deletes (the product `audit_log` is document-keyed), and
  file sharing (the `documents.shares` primitive is already polymorphic — `resource_type='file'`).

## Security

- RLS is the tenant boundary (never an app `WHERE`); capability is checked app-side from the
  Principal. `/share` is the only unauthenticated path — `resolve_share` (owned by `service_role`,
  token-only, never a caller-supplied id) is its entire boundary.
- `audit_log` is append-only (INSERT-only grant). Storage is private; no stable public URL.

---

## Appendix — full schema, RLS & share function

Postgres DDL for the tables this spec owns, **reconciled to canonical `documents.*`**
(`proposed-schema.sql`): `uuid` PKs for `files`/`documents` (+ `documents.uid text` for API paths),
`tenant_id int` → `auth.tenants`, actor refs `uuid` → `auth.users`, the client link `contact_id int`
→ `crm.contacts` (bound in `0015`), claim-driven RLS via the `auth.*` helpers. Forward references to
tables that land later — `template_version_id`, `run_id` (generation, P2) and `account_id`,
`application_id` (`crm`/`platform`) — are plain columns here; their FKs are added by those migrations
(shown commented). Sharing is **not** here: it uses `documents.shares` (see
[Sharing](#sharing-documentsshares)). Tables first, then policies

- grants (the `documents_read` policy references `document_contacts`, so all tables must exist). The
  request path runs as `authenticated`; `service_role` is BYPASSRLS (worker + the share-function
  owner); `migrator` runs the DDL and is a member of `service_role`.

```sql
CREATE SCHEMA IF NOT EXISTS documents;
GRANT USAGE ON SCHEMA documents TO anon, authenticated, service_role;

-- ── Enums (canonical) ────────────────────────────────────────────────────────
-- Matches proposed-schema.sql exactly. No 'pending_action': "action needed" is
-- derived from open document_tasks (avoids the reference's cancel-stranding bug).
CREATE TYPE documents.document_status AS ENUM (
    'queued', 'generating', 'generated', 'failed', 'superseded', 'archived'
);

-- ════════════════════════════ TABLES ════════════════════════════════════════
-- files — canonical (uuid PK, no uid). Path-addressed, immutable once written.
CREATE TABLE documents.files (
    id           uuid        PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    bucket       text        NOT NULL,
    storage_key  text        NOT NULL,  -- uploads/{tenantId}/{docUid}/original.{ext}
                                        -- documents/{tenantId}/{docUid}/v{n}.{pdf|html}
    content_type text        NOT NULL,
    size_bytes   bigint      NOT NULL,
    sha256       text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT files_bucket_key_key UNIQUE (bucket, storage_key)
);
CREATE INDEX files_tenant_id_idx ON documents.files(tenant_id);
CREATE INDEX files_sha256_idx ON documents.files(sha256);

-- documents — canonical, with the minimum additions the upload base needs:
--   * source              (canonical is generation-only; we admit uploaded docs)
--   * template_version_id  is NULLABLE here (canonical: NOT NULL) — uploads have none
--   * name                (display/filename for uploaded docs)
-- Generation-only columns are written only by the render worker (generation spec);
-- it must not ship a competing migration for this table.
CREATE TABLE documents.documents (
    id                   uuid                      PRIMARY KEY DEFAULT uuid_generate_v4(),
    uid                  text                      NOT NULL UNIQUE,   -- public id; API paths use this
    tenant_id            int                       NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    template_version_id  int,                      -- FK → documents.template_versions(id) added by the generation spec (P2)
    run_id               int,                      -- FK → documents.document_runs(id) added by the generation spec (P2)
    account_id           int,                      -- FK → platform.accounts(id) added in 0002
    application_id       int,                      -- FK → crm.applications(id) added in 0002
    original_file_id     uuid                      REFERENCES documents.files(id) ON DELETE SET NULL,  -- uploaded original (any allowed type)
    pdf_file_id          uuid                      REFERENCES documents.files(id) ON DELETE SET NULL,
    html_file_id         uuid                      REFERENCES documents.files(id) ON DELETE SET NULL,
    superseded_by        uuid                      REFERENCES documents.documents(id) ON DELETE SET NULL,
    generated_by         uuid                      REFERENCES auth.users(id),
    document_type        text                      NOT NULL,
    source               text                      NOT NULL DEFAULT 'generated' CHECK (source IN ('generated', 'uploaded')),  -- ADDED
    name                 text,                                                                                                 -- ADDED (uploaded-doc display name)
    status               documents.document_status NOT NULL DEFAULT 'queued',
    inputs               jsonb                     NOT NULL DEFAULT '{}',
    dataset_snapshots    jsonb                     NOT NULL DEFAULT '{}',
    calculation_outputs  jsonb                     NOT NULL DEFAULT '{}',
    metadata             jsonb                     NOT NULL DEFAULT '{}',
    calc_lib_version     text,
    renderer_version     text,
    deletable            boolean                   NOT NULL DEFAULT true,   -- ADDED (0122)
    error                text,
    generated_at         timestamptz,
    created_at           timestamptz               NOT NULL DEFAULT now()
);
CREATE INDEX documents_tenant_id_idx    ON documents.documents(tenant_id);
CREATE INDEX documents_type_created_idx ON documents.documents(document_type, created_at DESC);
CREATE INDEX documents_status_idx       ON documents.documents(status);
CREATE INDEX documents_inputs_gin       ON documents.documents USING gin (inputs);

-- document_contacts — canonical. The client-scope link (drives client visibility). tenant_id is
-- denormalised so document_contacts_read scopes off the local columns (no recursing subquery into
-- documents). Bound to crm.contacts in 0015_documents_crm_contacts.
CREATE TABLE documents.document_contacts (
    document_id uuid        NOT NULL REFERENCES documents.documents(id) ON DELETE CASCADE,
    tenant_id   int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    contact_id  int         NOT NULL REFERENCES crm.contacts(id) ON DELETE CASCADE,
    role        text        NOT NULL DEFAULT 'recipient',
    added_by    uuid        REFERENCES auth.users(id),
    added_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, contact_id, role)
);
CREATE INDEX document_contacts_contact_id_idx ON documents.document_contacts(contact_id);

-- audit_log — canonical. Product-semantic actions (pdf_viewed, downloaded, shared,
-- task_*) the DML trigger can't see. Append-only; kept DISJOINT from the 0001 DML trail.
CREATE TABLE documents.audit_log (
    id          bigint      PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    tenant_id   int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    document_id uuid        REFERENCES documents.documents(id) ON DELETE SET NULL,
    template_id int,        -- FK → documents.templates(id) added by the generation spec (P2)
    actor_id    uuid        REFERENCES auth.users(id),
    action      text        NOT NULL,
    detail      jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX documents_audit_log_tenant_id_idx ON documents.audit_log(tenant_id);
CREATE INDEX documents_audit_log_document_idx  ON documents.audit_log(document_id, created_at DESC);

-- ── Base-module tables (not yet in proposed-schema.sql — propose folding in) ──
-- document_tasks — client action items. task_type/status app-enforced (Pydantic).
CREATE TABLE documents.document_tasks (
    id           int         PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    uid          text        NOT NULL UNIQUE,
    tenant_id    int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    document_id  uuid        NOT NULL REFERENCES documents.documents(id) ON DELETE CASCADE,
    contact_id   int         NOT NULL,  -- FK → crm.contacts(id) bound in 0015; int to match contact_scope()
    assigned_by  uuid        NOT NULL REFERENCES auth.users(id),
    task_type    text        NOT NULL CHECK (task_type IN ('review', 'acknowledge', 'upload_requested')),
    message      text,
    due_date     date,
    status       text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'cancelled')),
    completion_data jsonb,
    completed_at timestamptz,
    completed_by uuid REFERENCES auth.users(id),
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX document_tasks_document_idx ON documents.document_tasks(document_id);
CREATE INDEX document_tasks_contact_idx  ON documents.document_tasks(contact_id, status);

-- document_tags — no tenant_id; scoped by parent visibility (see policy below).
CREATE TABLE documents.document_tags (
    document_id uuid        NOT NULL REFERENCES documents.documents(id) ON DELETE CASCADE,
    tag         text        NOT NULL CHECK (char_length(tag) <= 64),
    tag_type    text        NOT NULL DEFAULT 'free' CHECK (tag_type IN ('period', 'category', 'free')),
    applied_by  uuid        NOT NULL REFERENCES auth.users(id),
    applied_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, tag)
);

-- requests + request_items — adviser asks a client to upload / agree / acknowledge / complete a
-- form (0007's upload-only `upload_requests` was DROPPED and replaced by this header + line-items
-- shape in 0008_document_requests_v2). The client is bound by `contact_id int → crm.contacts` (the
-- true client model, reconciled in 0015_documents_crm_contacts); `requested_by` stays a `uuid →
-- auth.users` (the STAFF member who raised the request — an actor, not a client).
CREATE TABLE documents.requests (
    id            int         PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    uid           text        NOT NULL UNIQUE,
    tenant_id     int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,
    contact_id    int         NOT NULL REFERENCES crm.contacts(id) ON DELETE CASCADE,
    requested_by  uuid        NOT NULL REFERENCES auth.users(id),
    priority      text        NOT NULL DEFAULT 'normal'
                      CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    message       text,
    due_date      date,
    status        text        NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'fulfilled', 'cancelled')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    completed_at  timestamptz
);
CREATE INDEX requests_tenant_idx ON documents.requests(tenant_id);
CREATE INDEX requests_contact_idx ON documents.requests(contact_id, status);

-- request_items — one concrete thing the client must do; items are fulfilled independently and the
-- parent flips to 'fulfilled' once none remain pending. `tenant_id` is denormalised so the RLS
-- write policy stays off a recursing subquery. File-backed actions (upload) create a document →
-- fulfilled_document_id; confirm-only actions (agree / acknowledge) act on target_document_id;
-- complete_form stores a form_response (no document). NB: the CHECK constraint still admits the
-- legacy 'sign' value — the app layer standardised on 'agree' (view + tick "I agree"; real signing
-- is a later addition) and creates only upload/agree/acknowledge/complete_form items.
CREATE TABLE documents.request_items (
    id                    int         PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY,
    uid                   text        NOT NULL UNIQUE,
    request_id            int         NOT NULL REFERENCES documents.requests(id) ON DELETE CASCADE,
    tenant_id             int         NOT NULL REFERENCES auth.tenants(id) ON DELETE CASCADE,  -- denormalised for RLS
    action                text        NOT NULL
                              CHECK (action IN ('upload', 'sign', 'acknowledge', 'complete_form')),
    title                 text        NOT NULL,
    category              text,
    format_hint           text,
    form_uid              text,  -- complete_form: the builder form to render (added in 0010)
    target_document_id    uuid        REFERENCES documents.documents(id) ON DELETE SET NULL,
    fulfilled_document_id uuid        REFERENCES documents.documents(id) ON DELETE SET NULL,
    status                text        NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'fulfilled', 'cancelled')),
    fulfilled_at          timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX request_items_request_idx ON documents.request_items(request_id);
CREATE INDEX request_items_tenant_idx ON documents.request_items(tenant_id, status);

-- ══════════════════════════ RLS + GRANTS ═════════════════════════════════════
-- Pattern is the 0001 two-layer model. A 'client' principal has an EMPTY
-- auth.tenant_lineage() (the 0001 safety lever), so it never matches a staff
-- predicate and is reachable ONLY via the contact_scope branch.

-- documents: read-all staff see the firm; the creator sees their own; own-book
-- advisers + clients see docs linked to a contact in their scope.
ALTER TABLE documents.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.documents FORCE ROW LEVEL SECURITY;
CREATE POLICY documents_read ON documents.documents FOR SELECT TO authenticated USING (
    (tenant_id = ANY (auth.tenant_lineage()) AND auth.can_read_all())
    OR generated_by = auth.uid()
    OR EXISTS (SELECT 1 FROM documents.document_contacts dc
               WHERE dc.document_id = documents.documents.id
                 AND dc.contact_id = ANY (auth.contact_scope()))
);
CREATE POLICY documents_write ON documents.documents FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage())
           AND (auth.can_read_all() OR generated_by = auth.uid()
                OR EXISTS (SELECT 1 FROM documents.document_contacts dc
                           WHERE dc.document_id = documents.documents.id
                             AND dc.contact_id = ANY (auth.contact_scope()))))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents.documents TO authenticated;
GRANT ALL ON documents.documents TO service_role;

-- files: staff by tenant; clients see a file iff a visible document references it
-- (the EXISTS runs under documents' RLS, inheriting that visibility).
ALTER TABLE documents.files ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.files FORCE ROW LEVEL SECURITY;
CREATE POLICY documents_files_read ON documents.files FOR SELECT TO authenticated USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR EXISTS (SELECT 1 FROM documents.documents d
               WHERE d.pdf_file_id = documents.files.id OR d.html_file_id = documents.files.id)
);
CREATE POLICY documents_files_write ON documents.files FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents.files TO authenticated;
GRANT ALL ON documents.files TO service_role;

-- document_tasks: staff by tenant; the assigned client/own-book adviser by contact_scope.
ALTER TABLE documents.document_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.document_tasks FORCE ROW LEVEL SECURITY;
CREATE POLICY document_tasks_read ON documents.document_tasks FOR SELECT TO authenticated USING (
    tenant_id = ANY (auth.tenant_lineage())
    OR contact_id = ANY (auth.contact_scope())
);
CREATE POLICY document_tasks_write ON documents.document_tasks FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()) OR contact_id = ANY (auth.contact_scope()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()) OR contact_id = ANY (auth.contact_scope()));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents.document_tasks TO authenticated;
GRANT ALL ON documents.document_tasks TO service_role;

-- Child tables with no tenant_id: scope by PARENT visibility (the inner SELECT
-- is evaluated under the parent's RLS, so the child inherits it exactly).
ALTER TABLE documents.document_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.document_tags FORCE ROW LEVEL SECURITY;
CREATE POLICY document_tags_via_parent ON documents.document_tags FOR ALL TO authenticated
    USING (EXISTS (SELECT 1 FROM documents.documents d WHERE d.id = document_id))
    WITH CHECK (EXISTS (SELECT 1 FROM documents.documents d WHERE d.id = document_id));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents.document_tags TO authenticated;
GRANT ALL ON documents.document_tags TO service_role;

-- document_contacts: staff by tenant; the linked client reads their own via contact_scope. Scoped by
-- the local tenant_id/contact_id columns (no subquery into documents) to avoid RLS recursion.
ALTER TABLE documents.document_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.document_contacts FORCE ROW LEVEL SECURITY;
CREATE POLICY document_contacts_read ON documents.document_contacts FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()) OR contact_id = ANY (auth.contact_scope()));
CREATE POLICY document_contacts_write ON documents.document_contacts FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents.document_contacts TO authenticated;
GRANT ALL ON documents.document_contacts TO service_role;

-- requests: staff manage the tenant's; the target client may READ their own (its contact is in the
-- caller's contact_scope) to see + fulfil them. Clients never write directly — they fulfil via the
-- service_role endpoint — so writes are staff-only. request_items reads reach the client through a
-- ONE-WAY subquery into `requests` (no recursion: `requests` policies never reference `request_items`).
ALTER TABLE documents.requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.requests FORCE ROW LEVEL SECURITY;
CREATE POLICY requests_read ON documents.requests FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()) OR contact_id = ANY (auth.contact_scope()));
CREATE POLICY requests_write ON documents.requests FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents.requests TO authenticated;
GRANT ALL ON documents.requests TO service_role;

ALTER TABLE documents.request_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.request_items FORCE ROW LEVEL SECURITY;
CREATE POLICY request_items_read ON documents.request_items FOR SELECT TO authenticated
    USING (
        tenant_id = ANY (auth.tenant_lineage())
        OR EXISTS (SELECT 1 FROM documents.requests r
                   WHERE r.id = request_id AND r.contact_id = ANY (auth.contact_scope()))
    );
CREATE POLICY request_items_write ON documents.request_items FOR ALL TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()))
    WITH CHECK (tenant_id = ANY (auth.tenant_lineage()));
GRANT SELECT, INSERT, UPDATE, DELETE ON documents.request_items TO authenticated;
GRANT ALL ON documents.request_items TO service_role;

-- audit_log: tenant-scoped read; INSERT-only for the request role (append-only).
ALTER TABLE documents.audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents.audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY documents_audit_read ON documents.audit_log FOR SELECT TO authenticated
    USING (tenant_id = ANY (auth.tenant_lineage()));
CREATE POLICY documents_audit_append ON documents.audit_log FOR INSERT TO authenticated WITH CHECK (true);
GRANT SELECT, INSERT ON documents.audit_log TO authenticated;  -- no UPDATE/DELETE (append-only)
GRANT ALL ON documents.audit_log TO service_role;

GRANT USAGE ON ALL SEQUENCES IN SCHEMA documents TO authenticated, service_role;

-- ── Public share function (SECURITY DEFINER) ──────────────────────────────────
-- The only path that reads a document with no org session. Takes ONLY the token —
-- never a document/tenant id from the caller. Resolves against the CANONICAL
-- crm.shares primitive (resource_type='document', resource_uid = the document uid).
-- Requires crm.shares + the added resource_uid column (see Sharing), so it ships
-- with/after the crm migration. Owned by service_role (BYPASSRLS) so it crosses RLS.
CREATE OR REPLACE FUNCTION documents.resolve_share(p_token text)
RETURNS TABLE (
    document_uid text,
    bucket       text,
    storage_key  text,
    content_type text,
    expires_at   timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = documents, crm, pg_catalog
AS $$
DECLARE
    v_share crm.shares;
BEGIN
    SELECT * INTO v_share FROM crm.shares WHERE token = p_token;

    IF NOT FOUND
       OR v_share.resource_type <> 'document'
       OR v_share.revoked_at IS NOT NULL
       OR (v_share.expires_at IS NOT NULL AND v_share.expires_at < now())
    THEN
        RAISE EXCEPTION 'share_not_found' USING ERRCODE = 'P0002';
    END IF;

    INSERT INTO crm.share_accesses (share_id, succeeded)
    VALUES (v_share.id, true);

    UPDATE crm.shares
    SET access_count      = access_count + 1,
        first_accessed_at = COALESCE(first_accessed_at, now()),
        last_accessed_at  = now()
    WHERE id = v_share.id;

    RETURN QUERY
    SELECT d.uid, f.bucket, f.storage_key, f.content_type, v_share.expires_at
    FROM documents.documents d
    JOIN documents.files f ON f.id = d.pdf_file_id
    WHERE d.uid = v_share.resource_uid;
END;
$$;

-- Reassign to the BYPASSRLS role (migrator ∈ service_role, so this ALTER succeeds).
ALTER FUNCTION documents.resolve_share(text) OWNER TO service_role;
REVOKE ALL ON FUNCTION documents.resolve_share(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION documents.resolve_share(text) TO anon, authenticated;

-- ── DML audit trail (free, from 0001) ─────────────────────────────────────────
-- The generic 0001 trigger captures every write (incl. out-of-band worker/psql
-- writes) into audit.log. documents.audit_log above is for product-level events only.
-- NB: audit.record() casts row id to bigint, so uuid document rows get entity_id=NULL
-- (full row still in detail); an audit.log.entity_uid column is the deferred fix.
SELECT audit.attach(ARRAY[
    'documents.documents', 'documents.document_tasks',
    'documents.requests', 'documents.request_items'
]);

-- ── Domain events (emitted into the shared bus — NOT a documents-owned table) ──
-- Documents publish semantic events into the platform-wide events.events (defined in
-- proposed-schema.sql's `events` schema; consumed by the same app/events.py machinery
-- as the WorkOS stream). There is deliberately no documents.events table — the bus is
-- shared. events.events binds only via application_id today, so ONE additive change
-- (belongs to the events migration, not this one) lets it reference a uuid document,
-- mirroring crm.shares.resource_uid:
--
--   ALTER TABLE events.events
--       ADD COLUMN resource_type text,
--       ADD COLUMN resource_uid  text;   -- ('document', documents.uid)
--
-- Emit pattern (app-side, within tenant_session) on upload / share / generate / sign:
--   INSERT INTO events.events (tenant_id, event_type, resource_type, resource_uid, payload, created_by)
--   VALUES (:tenant, 'document.shared', 'document', :doc_uid, :payload, auth.uid());
```

---

## Out of scope (v1)

Passphrase-protected shares, bulk ZIP download, inline document comments, document expiry dates,
email-on-task-completion, and admin cross-tenant oversight beyond read and audit. Document generation (the render worker, templates,
datasets) is a separate spec and depends on the worker and queue.
