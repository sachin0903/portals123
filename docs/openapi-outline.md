# OpenAPI — setup & prospective outline

An outline of how the API's OpenAPI schema is produced, consumed, and where it's heading. Reference,
not a contract; the FastAPI app is the source of truth. Companion to `docs/api.md` (deeper endpoint
docs) and `docs/document-management.md` / `docs/forms.md`.

## 1. Pipeline (how the schema is produced & consumed)

- **Source of truth:** the FastAPI app (`apps/api/app/main.py`). Python types → Pydantic models →
  OpenAPI. Never hand-edit the generated client.
- **Schema endpoint:** FastAPI serves `GET /openapi.json` (+ Swagger UI at `/docs`). App metadata:
  `title="Graphene Platform API"`, `version="0.0.0"`.
- **Operation ids:** `custom_generate_unique_id` → `"{tag}-{route.name}"` (e.g. `documents-list-documents`).
  This drives clean method names in the generated TS client, so every route carries an explicit
  `name=` and a router `tags=[...]`.
- **Typed client:** Hey API (`@hey-api/openapi-ts`, `@hey-api/client-fetch`) generates
  `@graphene/api-client` into `pkgs/api-client/src/{client,sdk,types}.gen.ts`.
  - Config: `pkgs/api-client/openapi-ts.config.ts`; input = `OPENAPI_URL` env
    (default `http://localhost:8000/openapi.json`; CI points it at a dumped `apps/api/openapi.json`).
  - Regenerate: `moon run api-client:generate` (needs the API running or a dumped schema).
  - **CI fails on drift** — any schema change must be committed with a regenerated client.
- **Permissions (parallel generator, not OpenAPI):** `app/permissions.py` is the RBAC registry;
  `moon run api:generate-permissions` emits `@graphene/api-client/permissions` (typed `PermissionSlug`
  - shared `can`). Also drift-checked in CI.
- **Consumers:** the three SvelteKit portals. Note the documents portals mostly call the API through
  **SvelteKit server actions** (`+page.server.ts` `fetch`) rather than the generated SDK directly;
  the generated client is the typed contract + used elsewhere.

## 2. Cross-cutting conventions

- **Auth:** bearer JWT (WorkOS-verified); requests run under Postgres RLS via `tenant_session`
  (`SET LOCAL ROLE authenticated` + `request.jwt.claims`). Client-owned writes run elevated under
  `firm_session` (service_role) with an explicit ownership check as the auth boundary.
- **Authorisation:** per-endpoint `require_permission(...)` using typed constants (write ⇒ read;
  `system` bypasses). Reads are usually RLS-gated only.
- **Ids:** external string uids — a 12-character nanoid, no prefix (`app/ids.py::new_id`);
  internal int/bigint ids never leave the API. The one held-back kind is the tenant uid, still
  `ten_<hex>` because it becomes an S3 bucket name and a SQL schema identifier.
- **Errors:** FastAPI default `{ "detail": ... }`; portals surface `detail`.
- **Status codes:** `201` on create/upload/fulfil; `200` elsewhere; `404`/`422`/`403`/`503` as usual.
- **Dates:** `datetime` for timestamps (`created_at`, `at`), `date` for calendar fields (`due_date`).
- **Not yet standardised:** no pagination envelope (list endpoints return arrays; `/activity` takes a
  `limit` only); no cursor; no ETag/versioning.

## 3. Current surface — full endpoint list

`operationId = "{tag}-{route.name}"` (the TS client method name). Routers registered in `main.py`:
`health, me, admin, firm, documents, internal`. **`internal` is `include_in_schema=False`** — it is
**not** in `openapi.json` or the generated client (service-to-service, `require_internal_token`).

### health (`tags=["health"]`, no prefix)

| Method & path | operationId    |
| ------------- | -------------- |
| `GET /health` | `health-check` |

### me (`tags=["auth"]`, no prefix)

| Method & path         | operationId           |
| --------------------- | --------------------- |
| `GET /me`             | `auth-me`             |
| `GET /me/tenant`      | `auth-me-tenant`      |
| `GET /me/memberships` | `auth-me-memberships` |

### admin (`prefix="/admin"`, `tags=["admin"]`) — Internal Operations; `admin_session` + granular ops perms

| Method & path                                                          | operationId                          |
| ---------------------------------------------------------------------- | ------------------------------------ |
| `GET /admin/tenants`                                                   | `admin-list-tenants`                 |
| `POST /admin/tenants`                                                  | `admin-create-tenant`                |
| `GET /admin/tenants/{tenant_id}`                                       | `admin-get-tenant`                   |
| `PATCH /admin/tenants/{tenant_id}`                                     | `admin-update-tenant`                |
| `POST /admin/tenants/{tenant_id}/sync`                                 | `admin-sync-tenant`                  |
| `GET /admin/tenants/{tenant_id}/environment`                           | `admin-get-environment`              |
| `PUT /admin/tenants/{tenant_id}/environment`                           | `admin-register-environment`         |
| `PUT /admin/environments/{environment_id}/secrets`                     | `admin-set-environment-secrets`      |
| `POST /admin/environments/refresh`                                     | `admin-refresh-environment-registry` |
| `GET /admin/tenants/{tenant_id}/branding`                              | `admin-get-branding`                 |
| `PUT /admin/tenants/{tenant_id}/branding`                              | `admin-set-branding`                 |
| `POST /admin/tenants/{tenant_id}/branding/asset`                       | `admin-upload-branding-asset`        |
| `GET /admin/tenants/{tenant_id}/roles`                                 | `admin-list-tenant-roles`            |
| `POST /admin/tenants/{tenant_id}/roles`                                | `admin-create-tenant-role`           |
| `PUT /admin/tenants/{tenant_id}/roles/{slug}/permissions`              | `admin-set-tenant-role-permissions`  |
| `GET /admin/tenants/{tenant_id}/members`                               | `admin-list-tenant-members`          |
| `POST /admin/tenants/{tenant_id}/members`                              | `admin-create-tenant-member`         |
| `PUT /admin/tenants/{tenant_id}/members/{user_id}/portals/{portal}`    | `admin-grant-member-portal`          |
| `DELETE /admin/tenants/{tenant_id}/members/{user_id}/portals/{portal}` | `admin-revoke-member-portal`         |
| `PATCH /admin/tenants/{tenant_id}/members/{user_id}/portals/{portal}`  | `admin-set-member-portal-role`       |
| `POST /admin/tenants/{tenant_id}/members/{user_id}/deactivate`         | `admin-deactivate-tenant-member`     |
| `POST /admin/tenants/{tenant_id}/members/{user_id}/reactivate`         | `admin-reactivate-tenant-member`     |
| `POST /admin/tenants/{tenant_id}/members/{user_id}/resend-invite`      | `admin-resend-tenant-member-invite`  |
| `GET /admin/users`                                                     | `admin-list-users`                   |
| `GET /admin/users/{user_id}`                                           | `admin-get-user`                     |
| `GET /admin/permissions`                                               | `admin-list-permissions`             |
| `GET /admin/audit`                                                     | `admin-list-audit`                   |
| `GET /admin/model`                                                     | `admin-get-model-tenant`             |
| `GET /admin/operations`                                                | `admin-get-operations`               |

### firm (`prefix="/firm"`, `tags=["firm"]`) — platform-portal self-service; `firm_session` + `auth.require_permission`

| Method & path                                     | operationId                  |
| ------------------------------------------------- | ---------------------------- |
| `GET /firm/members`                               | `firm-list-members`          |
| `POST /firm/members`                              | `firm-invite-member`         |
| `PUT /firm/members/{user_id}/portals/{portal}`    | `firm-grant-portal`          |
| `DELETE /firm/members/{user_id}/portals/{portal}` | `firm-revoke-portal`         |
| `PATCH /firm/members/{user_id}/portals/{portal}`  | `firm-set-portal-role`       |
| `POST /firm/members/{user_id}/deactivate`         | `firm-deactivate-member`     |
| `POST /firm/members/{user_id}/reactivate`         | `firm-reactivate-member`     |
| `POST /firm/members/{user_id}/resend-invite`      | `firm-resend-invite`         |
| `GET /firm/roles`                                 | `firm-list-roles`            |
| `POST /firm/roles`                                | `firm-create-role`           |
| `PUT /firm/roles/{slug}/permissions`              | `firm-set-role-permissions`  |
| `GET /firm/permissions`                           | `firm-list-permissions`      |
| `GET /firm/branding`                              | `firm-get-branding`          |
| `PUT /firm/branding`                              | `firm-set-branding`          |
| `POST /firm/branding/asset`                       | `firm-upload-branding-asset` |

### internal (`prefix="/internal"`, `include_in_schema=False`) — NOT in OpenAPI / client

| Method & path                  | operationId |
| ------------------------------ | ----------- |
| `GET /internal/env-config`     | (excluded)  |
| `GET /internal/operations-org` | (excluded)  |

## 4. Documents & forms (this branch)

`prefix="/documents"`, `tags=["documents"]`. Reads RLS-gated; writes gated `documents:write`
(fulfil/form-response run elevated under `firm_session` with a client-ownership check).

| Method & path                                                                  | operationId                              |
| ------------------------------------------------------------------------------ | ---------------------------------------- |
| `GET /documents`                                                               | `documents-list-documents`               |
| `POST /documents/upload`                                                       | `documents-upload-document`              |
| `GET /documents/clients`                                                       | `documents-list-document-clients`        |
| `GET /documents/activity`                                                      | `documents-recent-document-activity`     |
| `GET /documents/requests`                                                      | `documents-list-document-requests`       |
| `POST /documents/requests`                                                     | `documents-create-document-request`      |
| `PATCH /documents/requests/{req_uid}`                                          | `documents-update-document-request`      |
| `POST /documents/requests/{req_uid}/cancel`                                    | `documents-cancel-document-request`      |
| `POST /documents/requests/{req_uid}/items/{item_uid}/fulfill`                  | `documents-fulfill-request-item`         |
| `POST /documents/requests/{req_uid}/items/{item_uid}/form-response`            | `documents-submit-request-form-response` |
| `POST /documents/requests/{req_uid}/items/{item_uid}/reassign`                 | `documents-reassign-request-item`        |
| `GET /documents/{uid}`                                                         | `documents-get-document`                 |
| `PATCH /documents/{uid}`                                                       | `documents-update-document`              |
| `POST /documents/{uid}/send`                                                   | `documents-send-document`                |
| `POST /documents/{uid}/archive`                                                | `documents-archive-document`             |
| `GET /documents/{uid}/url`                                                     | `documents-document-url`                 |
| `GET /documents/{uid}/content`                                                 | `documents-document-content`             |
| `GET /documents/{uid}/activity`                                                | `documents-document-activity`            |
| `GET /documents/{uid}/requests`                                                | `documents-list-requests-for-document`   |
| `GET /documents/{uid}/versions` — _planned_                                    | `documents-list-document-versions`       |
| `POST /documents/{uid}/tags` — _planned_                                       | `documents-add-document-tag`             |
| `DELETE /documents/{uid}/tags/{tag}` — _planned_                               | `documents-remove-document-tag`          |
| `GET /documents/requests/{req_uid}/items/{item_uid}/form-response` — _planned_ | `documents-get-request-form-response`    |
| `POST /documents/{uid}/share` — _planned (needs crm)_                          | `documents-share-document`               |

Notes:

- `GET /documents` takes `?user_id=` to scope to one client (interim `document_contacts` link);
  `GET /documents/activity` takes `?limit=` (1–100).
- Request items carry `action` (`upload|agree|acknowledge|complete_form`), `title`, `category`,
  `format_hint`, **`form_uid`**, `target_document_uid`.
- `…/fulfill` file actions create a document; `agree`/`acknowledge` confirm against the target.
  `…/form-response` writes `documents.form_responses` (no document). **No GET for responses yet** (§5).
- Product events → `documents.audit_log` — the per-document **activity feed**, _not_ the
  `events.events` notification bus (see gaps): `document.uploaded|sent|viewed|updated|archived|agreed|
acknowledged|completed|reassigned`.
- Rows marked _planned_ are in the documented spec (`document-management.md`) but not built yet;
  `…/share` also depends on `crm.shares` (0002). `GET /{uid}/activity` is effectively staff-only.
- Storage/schema: migrations `0005_documents_storage` → `0008_document_requests_v2`,
  `0010_form_responses`; S3-compatible bucket (`S3_DOCUMENTS_BUCKET`).

### Redundancies

**✓ Resolved:**

- **`tasks` consolidated into `requests`.** `document_tasks` + the `/tasks` endpoints are removed (from
  the router and migration `0006_document_collab`), replaced by **`GET /documents/{uid}/requests`**
  (`documents-list-requests-for-document`) — returns `RequestOut[]` for requests whose item targets
  this document (`target_document_id`) or was fulfilled by it. Platform doc-detail (assign-task dialog)
  and client task UIs migrated onto requests; the mock client `/tasks/[id]` route was deleted. A
  separate primitive can return later for internal (staff-to-staff) review.
- **`complete_form` single-pathed.** Dropped from `_FILE_ACTIONS`; `fulfill` now 422s it, so
  `…/form-response` (jsonb answers) is its only fulfilment path.
- **Resend → `…/reassign`.** `POST /documents/requests/{req_uid}/items/{item_uid}/reassign` swaps the
  document attached to an agree/acknowledge item (fix a wrong attachment). Pending → re-points +
  notifies (item reappears in the client's portal). Already-agreed → **re-opens the same item** and
  records `document.agreement_superseded` on the old doc (append-only audit; prior consent kept as a
  fact, never overwritten). No schema change — the audit log is the trail.

**Still open (documented features not built):**

- **Domain events / notifications.** Documents should emit `document.uploaded|sent|shared|generated`
  into `events.events` (the notification outbox); today only `documents.audit_log` is written, so
  sending a document or creating a request never notifies the client. Highest-value gap.
- **Versioning / supersede.** `superseded_by` exists and the list excludes `superseded`, but there's no
  `GET /documents/{uid}/versions` and `upload` takes no `supersedes_uid` — nothing supersedes anything.
- **Tags.** `document_tags` is canonical but absent from the built migrations; no `…/tags` endpoint.
- **"Action needed" not projected.** Derive from open `request_items` targeting a doc; expose an
  `open_actions` count on `DocumentOut` (else the platform list N+1s `/{uid}/requests`).
- **Client can't read its own document's activity.** `documents_audit_read` is tenant-lineage-only, so
  `GET /{uid}/activity` is empty for clients even on their own doc — add a `document_contacts` branch.
- **Read form responses.** No GET for submitted answers yet (planned `…/form-response` GET).

**Also open:**

- **Remind → `POST /documents/requests/{uid}/remind`** _(prospective)_. Notify-only nudge about pending
  items, reusing the request's message + due date, with `last_reminded_at` + count + cooldown (audit
  `request.reminded`). Distinct from `reassign` (no state change). Manual first; due-date auto-reminders
  a v2. The "Remind" table button is a stub today.
- **`/{uid}/url` vs `/{uid}/content`.** Two ways to fetch the bytes — presigned redirect vs API proxy.
  Keep intentionally (direct download vs inline view) or standardise on one.
- **Action endpoints vs `PATCH`.** `POST /{uid}/archive` and `POST /requests/{uid}/cancel` are status
  transitions that could fold into `PATCH … {status}`. A convention call (RPC-style action endpoints
  give clearer audit + auth) — decide once and apply consistently.
- **Not redundant (deliberate):** `admin/*` vs `firm/*` member / role / branding endpoints are parallel
  by design — Graphene operations vs firm self-service, different auth (`admin_session` vs
  `firm_session`) and scope. `activity` (tenant) vs `{uid}/activity` (per-document) differ by scope too.

## 5. Prospective / gaps

Ordered roughly by value. "FE-mocked" = a real endpoint exists but the frontend still fakes it.

1. **`DocumentOut.uploaded_by` + `client`.** `generated_by` is recorded but not projected; the docs
   table stubs both. Add `LEFT JOIN auth.users` for the uploader name now; the real per-document
   **client** link waits on platform_crm (`documents.account_id` + `document_contacts` → `crm.*`).
   → schema change ⇒ regenerate client.
2. **Read form responses.** `POST …/form-response` exists but there's **no GET** — staff can't read a
   client's submitted answers yet. Add `GET /requests/{uid}/items/{item_uid}/form-response` (and/or a
   per-document view). → new endpoint.
3. **Reconcile FE mocks to live endpoints.** Client form submit (`submit-request-form-response`) and
   outstanding-task due-date edit (`update-document-request`) both exist server-side; swap the
   remaining optimistic/mock frontend paths onto them.
4. **platform_crm bindings (0002 forward).** Replace interim `document_contacts` with account/contact
   FKs; unlocks real client column, client-scoped RLS, and firmer send/share semantics
   (`crm.shares`, token-based external sharing).
5. **Pagination & filtering.** List endpoints (`""`, `/requests`, `/activity`) return unbounded arrays
   — add a cursor/limit envelope + server-side filters (source, status, search) before data grows.
6. **Client-library scoping.** The client page fetches the full firm library for the Send picker;
   revisit once account binding exists (send-any vs already-linked).
7. **Signing.** `agree` is confirm-only; real e-signature is a later action type + flow.

## 5a. Prospective — generation, datasets & forms (not built)

Templates/generation and forms are **frontend/mock only** today (GrapesJS editor, forms builder,
`demo.ts`); no router, schema, or persistence. Shapes follow `document-generation.md` + `forms.md` —
design targets, not a contract.

**Generation is async and lives on `/documents`** — there is _no_ `POST /templates/{uid}/generate`.
A generate is `POST /documents { template_uid, inputs, dataset_uids?, subject_ref? }` → inserts a
`documents` row `status='queued'`, enqueues to PGMQ, and the caller polls the existing
`GET /documents/{uid}` through `queued → generating → generated | failed`. The worker renders
(Jinja + WeasyPrint) and writes the files + a `document.generated` event.

**Templates** (authoring; published versions immutable):

| Method & path                                      | operationId                                                       |
| -------------------------------------------------- | ----------------------------------------------------------------- |
| `GET · POST /templates`                            | `list-templates` · `create-template`                              |
| `GET · PATCH · DELETE /templates/{uid}`            | `get/update/delete-template`                                      |
| `GET · POST /templates/{uid}/versions`             | `list-template-versions` · `create-template-version` (save draft) |
| `GET /templates/{uid}/versions/{vid}`              | `get-template-version`                                            |
| `GET /templates/{uid}/versions/{vid}/diff/{other}` | `diff-template-version`                                           |
| `POST /templates/{uid}/versions/{vid}/preview`     | `preview-template-version`                                        |
| `POST /templates/{uid}/versions/{vid}/publish`     | `publish-template-version`                                        |

**Datasets** (render-time data; versioned independently, pinned by templates):

| Method & path                                 | operationId                               |
| --------------------------------------------- | ----------------------------------------- |
| `GET · POST /datasets`                        | `list-datasets` · `create-dataset`        |
| `GET · DELETE /datasets/{uid}`                | `get-dataset` · `delete-dataset`          |
| `GET /datasets/{uid}/data`                    | `preview-dataset-data` (capped rows)      |
| `POST /datasets/{uid}/versions`               | `create-dataset-version` (csv/sql/static) |
| `POST /datasets/{uid}/versions/{vid}/publish` | `publish-dataset-version`                 |

**Forms** (staff form builder — frontend-only today):

| Method & path                       | operationId                  |
| ----------------------------------- | ---------------------------- |
| `GET · POST /forms`                 | `list-forms` · `create-form` |
| `GET · PATCH · DELETE /forms/{uid}` | `get/update/delete-form`     |
| `POST /forms/{uid}/publish`         | `publish-form`               |
| `GET · PUT /forms/{uid}/fields`     | `get/set-form-fields`        |
| `POST /forms/{uid}/responses`       | `create-form-response`       |

- **Version id is a `uid`** (`…/versions/{vid}`), not an integer ordinal. Published versions are
  **immutable** — only drafts are saved; `publish` is a validated transition (Jinja syntax check +
  undeclared-variable scan), never a `PUT`.
- **Datasets are first-class** — templates pin dataset uids; the latest published version resolves at
  render time and is snapshotted onto the document for reproducibility.
- **`form_uid` is dangling today** — forms live only in the frontend mock, so a `complete_form`
  request item's `form_uid` has no `forms` table/FK and there's no `GET /forms/{uid}` for the client
  portal to fetch the definition it renders.
- **Auth:** `templates:*` / `datasets:*` / `forms:*` permissions; generate gated by `documents:write`.
- New `templates.*` schema (`templates · template_versions · datasets · dataset_versions`), PGMQ
  (`documents_render` + DLQ), a Jinja2/WeasyPrint worker.

## 6. Change checklist (any schema edit)

1. Edit Pydantic models / routes in `apps/api`. 2. `moon run api:test :typecheck`.
2. Regenerate: `moon run api-client:generate` (API running). 4. Commit the regenerated
   `pkgs/api-client/src` (CI fails on drift). 5. Update `docs/api.md` + this outline if the surface
   changed.
