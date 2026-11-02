# Functional audit — July 2026

A function-by-function audit of the whole application: what each function is **expected** to do, what
it **actually** does today, and where the gaps are. Six domains were traced independently, end to end
— UI route → form action → API endpoint → service → SQL/RLS → worker — with file:line evidence for
every claim.

Statuses: ✅ working · ⚠️ partial/degraded · 🎭 demo/mock · ❌ missing · 💥 broken

> ## → [VERIFIED-STATUS.md](VERIFIED-STATUS.md) has the current, re-verified per-function status
>
> All 210 functions were re-checked against the code on 30 July 2026: **65 → 108 working, all 7
> broken fixed, demo/mock 22 → 5, zero regressions** — but 88 remain partial, 18 depend on
> migrations that may not be applied in production, and 12 newly-found issues were caused by the
> remediation itself.
>
> **The per-domain files below describe the state AT AUDIT TIME (28 July 2026), not now.** Three
> remediation waves have since landed (`804819b`, `91d98df`, `14140cf`): all 12 stop-the-line items
> addressed, ~200 findings closed, the API suite up from **342 to 1654 tests**, full gate green.
>
> **Migrations `0030`–`0035` are proven against the testcontainer chain but NOT applied to any live
> database.** They tighten RLS materially (scoping `auth.tenants`, book-scoping ten tables, changing
> what `is_system()` means, composite tenant-containment FKs), so applying them is a deliberate step.
> `apps/api/scripts/bootstrap_roles.sql` also gained `GRANT authenticated TO migrator`, which an
> existing database needs applied by a superuser — without it **every SQL dataset silently returns no
> rows at render**.
>
> ### Two lessons worth more than the fixes
>
> **1. A plausible diff is not a fix.** Of ~118 claimed fixes in wave 1, adversarial verification
> found **8 reported complete that were entirely inert**: a search endpoint never mounted, a route
> shadowed by a dynamic sibling, a hidden input read before Svelte flushed the microtask, a render
> callback nothing invoked, a SQL denylist defeated by quoting the identifier, a delete that 500'd on
> its own audit trigger, a kill switch nothing read. Every one would have passed code review. Trace
> the call path from the user action to the effect, and write the test that fails without the fix.
>
> **2. Watch for a control being "fixed" by being weakened.** Wave 3 closed more than it broke, but
> it also removed the only refusing limit from the share surface, removed the aggregate ceiling on
> outbound password-reset email, and opened a path to move money on an archived client. A fix that
> makes a guard stop refusing is not a fix. When a guard's problem is that it refuses the wrong
> people, re-key it — don't disarm it.

| #   | Domain                                                                    | Functions |  ✅ |  ⚠️ | 🎭  |  ❌ | 💥  | Verdict                                      |
| --- | ------------------------------------------------------------------------- | --------: | --: | --: | --- | --: | --- | -------------------------------------------- |
| 1   | [CRM](01-crm.md) — clients, dashboard, portfolio, KYC                     |        26 |   7 |  13 | 5   |   1 | –   | Real CRUD over fabricated money              |
| 2   | [Documents](02-documents.md) — library, upload, generate, send, requests  |        31 |  16 |  10 | 3   |   2 | –   | Strongest domain; requests engine has no UI  |
| 3   | [Authoring](03-authoring.md) — templates, editor, render, forms, datasets |        48 |  19 |  22 | 3   |   2 | 2   | Exceptional editor; unsafe SQL datasets      |
| 4   | [Journeys & tasks](04-journeys-tasks.md) — flows, orchestration, tasks    |        46 |   7 |  24 | 5   |   8 | 2   | Cannot complete its own seeded flow          |
| 5   | [Admin](05-admin.md) — tenants, members, roles, operations, branding      |        32 |  10 |  17 | 2   |   2 | 1   | Provisioning works; lifecycle controls don't |
| 6   | [Core](06-core.md) — auth, RLS, email, search, observability, deploy      |        23 |   7 |  11 | 2   |   2 | 1   | Excellent isolation design; no observability |
|     | **Total**                                                                 |   **206** |  66 |  97 | 20  |  17 | 6   | **32% complete, 47% partial**                |

## The headline

The platform's **foundations are genuinely strong** — the claims/RLS design, the document render
pipeline, the template editor, session refresh, and the permission registry are all better than
typical for this stage. What is weak is the **last mile**: the layer where a built backend meets a
user. Two thirds of the functions audited are partial, and they fail in seven recurring ways rather
than 200 unrelated ones. Fixing the patterns fixes most of the register.

**Nothing here is a "polish" list.** The most serious findings are functions that report success
while doing nothing, and screens that present fabricated data as the user's own.

## Stop the line — 12 items to fix before anyone real uses this

Ordered by blast radius, not effort.

| #      | Finding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Where                                                                                                                          |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **1**  | **Tenant-authored SQL runs with fabricated `contacts:read_all` claims.** Any holder of `datasets:read` can exfiltrate the firm's entire client book, bypassing their own book scoping. The "SELECT-only" guard is a `startswith` check on a raw string — it does not stop `; UPDATE`, writing functions, or `set_config('request.jwt.claims', …)` mid-query, which rewrites the GUC every RLS predicate reads at scan time. No `statement_timeout`, no read-only transaction, no row cap; a runaway query is reclaimed and re-run. | [resolve.py:106-150](../../apps/api/app/render/resolve.py:106)                                                                 |
| **2**  | **Change password is a lie on platform and admin.** Both validate the input, return `{passwordChanged: true}`, and never call WorkOS. Staff and Graphene ops believe they rotated a credential that is unchanged. The correct implementation already exists in the client portal.                                                                                                                                                                                                                                                  | [platform security:106](../../apps/platform/src/routes/account/security/+page.server.ts:106)                                   |
| **3**  | **`auth.tenants` reads with `USING (true)`.** Any authenticated principal — including a _client end-user of one firm_ — can enumerate every firm on the instance: name, alias, website, WorkOS project id, and the full branding `theme`. It bypasses the otherwise-excellent client-portal boundary.                                                                                                                                                                                                                              | [0001_auth_core.sql:577](../../apps/api/migrations/sql/0001_auth_core.sql:577)                                                 |
| **4**  | **Every money figure shown to advisers and clients is fabricated.** Holdings/valuations come from a stale manual `ingest_seccl.py` snapshot, then units and values are **rescaled** by `clientAum/lakeTotal` to make totals tie. Withdrawal then validates against **total account value, not cash**, while the same screen shows a different lake-derived Cash figure — so it will authorise a withdrawal the account cannot fund.                                                                                                | [01-crm.md](01-crm.md)                                                                                                         |
| **5**  | **A fresh install cannot grant a single permission.** The model tenant is seeded blank and no migration creates any role, so `clone_model_roles` clones nothing and every `require_permission` endpoint 403s. The documented break-glass ("find your id on the admin home's `/me` output") is unfollowable — no portal fetches `/me` — and SQL `auth.is_system()` doesn't honour `SYSTEM_USER_IDS` anyway, so break-glass passes application gates then sees nothing under RLS.                                                    | [0001_auth_core.sql:631](../../apps/api/migrations/sql/0001_auth_core.sql:631) · [auth.py:172](../../apps/api/app/auth.py:172) |
| **6**  | **The seeded onboarding flow cannot be completed.** Its staff-assigned `fill_form` and `review` steps have no fulfilment surface anywhere, so every journey started from the shipped seed stalls permanently. `generate_document` steps enqueue nothing, so any flow using one is stuck forever.                                                                                                                                                                                                                                   | [04-journeys-tasks.md](04-journeys-tasks.md)                                                                                   |
| **7**  | **Flag-off is a total outage, not a fallback.** `FLOWS_ORCHESTRATOR_V2` defaults to `False`, the v1 engine was deleted, and the flag is absent from `docker-compose.yaml` — so on the deployed stack journeys, `cases:*` and `/applications/*` are simply gone, while the UI instructs the user to set a flag they cannot set.                                                                                                                                                                                                     | [config.py:33](../../apps/api/app/config.py:33)                                                                                |
| **8**  | **All three search bars are fabricated, and no search endpoint exists.** An adviser searching their own client list is shown four fictional names, then dumped on `/clients`; an ops user sees five fictional firms. In a multi-tenant regulated product this is indistinguishable, to the user, from a cross-tenant data leak.                                                                                                                                                                                                    | [platform SearchBar:7-12](../../apps/platform/src/lib/SearchBar.svelte:7)                                                      |
| **9**  | **"Send to client" promises an email it never sends** and silently discards the covering message the adviser wrote. The document becomes visible in the portal; the client is never told.                                                                                                                                                                                                                                                                                                                                          | [02-documents.md](02-documents.md)                                                                                             |
| **10** | **Form validation and visibility conditions are client-side only** — both submission endpoints persist the answers blob unevaluated, so `required`, regex, lengths and bounds are bypassable by any direct API call. Client-side, `isEmptyValue(false) === false` means an **unticked required consent checkbox submits successfully**.                                                                                                                                                                                            | [validate.ts:6-12](../../packages/ui/src/lib/forms/editor/validate.ts:6)                                                       |
| **11** | **A missing, deleted, disabled or mistyped dataset silently produces a blank client document.** Resolution swallows every failure into `[]`, the lint marks every referenced code "known" so the generate gate can't see it, and dataset delete has no in-use guard. A firm can delete a dataset and keep producing plausible, empty-tabled suitability reports with no signal at any layer.                                                                                                                                       | [resolve.py:159-181](../../apps/api/app/render/resolve.py:159)                                                                 |
| **12** | **Zero error tracking and zero metrics, platform-wide.** No Sentry, no OTel, no Prometheus, text-only logs. `/health` is a static string, and the `render` worker — which hosts the render queue, the outbox dispatcher and the task notifier — has **no healthcheck at all**. A wedged worker, a stalled outbox, a Resend outage or a swallowed forgot-password failure are all invisible until a user complains.                                                                                                                 | [health.py:11](../../apps/api/app/routers/health.py:11) · [docker-compose.yaml:88](../../docker-compose.yaml:88)               |

## The seven patterns

Almost every ⚠️ in the register is an instance of one of these. They are worth fixing as patterns —
with a lint, a test, or a shared helper — rather than one call site at a time.

### 1. Silent failure is the default

The system's habitual response to a failure is to render a plausible success.

- `loadError` is computed **and never rendered** in documents, templates and datasets list pages —
  each with a comment promising a retry banner. An API outage presents as "you have no documents".
- Dataset resolution catches every failure into `[]` (blank charts and tables in a client PDF).
- The render worker writes a precise diagnostic into `documents.error`, then `DocumentOut` has no
  `error` field, so the user always sees the constant "Rendering failed. Check the template."
- `internal.py`'s forgot-password wraps the whole send in `except Exception` → INFO log. A total
  reset outage is invisible.
- Outbox dead letters and failed render jobs have no endpoint and no UI. Recovery needs psql.

**The fix is a rule, not 12 patches:** a loader that catches must return a flag the page renders, and
a queue that dead-letters must have a view. `LoadErrorBanner` already exists — it is simply not wired.

### 2. The UI promises what the backend doesn't do

Not missing features — _false_ ones. These are the trust-destroying class.

Change password (platform + admin) · Send-to-client's email · the tenant **Enabled** toggle (nothing
in any auth path reads `auth.tenants.enabled`) · the client details form (cannot clear any field, yet
toasts success) · a client's own account page (fabricated phone and address, edits validated then
discarded) · document tags and version history (reachable UI over absent backends) · the admin
`users/[id]` role change (PATCHes an endpoint that does not exist).

### 3. Built backend, absent UI

Substantial, tested capability with zero callers:

The entire document **requests** engine (create/cancel/amend/reassign/per-doc-view — and standalone
requests are unfulfillable because the client only ever sees flow case-steps) · template **archive**
(endpoint exists, no UI, no unarchive, and archived templates still generate) · render **retry** ·
dead-letter views · the `fill_form` step's form picker · CSV datasets · "Run query" on a dataset ·
all of `/me` · `/catalog/*` · ~14 orphan endpoints in total.

### 4. Declared-but-inert configuration

Fields and knobs the UI collects, the DB stores, and **nothing reads**:

`depends_on` on flow steps (validated, planned, persisted, read by nothing) · dataset `liveness` ·
dataset `scope` (a "subject" dataset that forgets `:subject_id` silently returns firm-wide rows into
one client's document; `collection` is entirely unimplemented) · `linked_datasets` (advisory only) ·
`flows.public_start_throttle` + `public_onboarding_max_per_hour` (the whole 0024 hardening, wired to
nothing, for an endpoint that doesn't exist) · `notify.*` (routed, no producer) · `sender_for()` (a
stub, so white-label is cosmetically complete but every email comes from Graphene) ·
`questionnaire_dataset_code`.

Each one reads, to a maintainer or an auditor, as a working control.

### 5. Immutability and enforcement by comment

`0012_templates.sql:43` comments "Immutable once status = 'published'" with no trigger or CHECK — and
the gap is reachable: `PATCH /templates/{uid}` writes `renderer` to the latest version regardless of
status, silently changing the render engine of a published version the UI calls immutable. Forms have
no versions at all, so editing a published form changes it under clients who are mid-fill.

### 6. Enforcement on the wrong side of the wire

Form validation and conditions (client-only) · permission gates (the platform portal renders
Documents, Templates, Forms and Clients **ungated** while every backing endpoint requires a
permission — so an under-permissioned user gets a permanently blank section with no explanation) ·
`tasks_write`'s `WITH CHECK` is tenant-wide, so book-scoping there is application-code-only.

### 7. Fabricated data in real, signed-in surfaces

Beyond the three search bars: every client sees the same fictional adviser · the client account page
shows a fake phone and postal address · document version history reads demo keys so it is permanently
empty on every real document · the transfer wizard runs on `demo.wrappers`. The naming compounds it —
importing `formatDate` from `$lib/demo` trains everyone to treat that module as legitimate, so
real-vs-fake is invisible at the call site.

## Governance and compliance gaps

This is a regulated-firm product; these are the findings a compliance reviewer would raise first.

- **`auth.role_permissions` is unaudited.** Permission grants and revocations leave no trace in
  `audit.log` — the single most sensitive administrative action in the system is invisible.
- **No tenant offboarding at all.** No suspend that works, no export, no deletion, no data-retention
  path. Combined with #3 above (the `enabled` toggle blocks nothing), a firm cannot be cut off.
- **Book-scoping is inconsistent on the most sensitive tables.** `crm.kyc_checks`
  (identity-verification outcomes), `flows.cases`/`case_steps`, and `documents.shares` (with full
  write) are tenant-wide, while the _documents_ attached to those same cases are correctly
  book-scoped. A book-scoped adviser reads every client's KYC and can revoke share links for
  documents they cannot see.
- **No journey can be cancelled**, and `cases_one_active_per_subject` then permanently locks
  re-onboarding for that subject.
- **No client archive or deactivate** — `deleted_at` is read but never written.
- **MFA has no recovery codes and no admin reset.** A user who loses their authenticator is
  permanently locked out.
- **`invitation_url` — a live credential — is returned in HTTP response bodies** and rendered in the
  admin and platform UIs, so it lands in proxy logs, browser history and screenshots.
- **No rate limiting on any unauthenticated surface**: login (password spraying), forgot-password
  (email bombing through our own Resend account), `/share/{token}` (enumeration).
- **Email verification is structurally broken** — `/verify` in all three portals gates on a cookie
  nothing sets, so it always redirects to `/login`; login now conflates verification with a password
  _reset_, resetting a working credential.
- **Isolation tests are missing** for `flows.*`, `crm.kyc_checks`, `documents.shares`,
  `documents.attachments` and `platform.instructions`; `tasks.tasks` is covered only via the API. And
  **the DB-backed suite skips rather than fails without Docker**, so the entire RLS isolation proof
  can silently not run behind a green gate.
- **Deploy migrates before it builds, then fires and forgets.** A build failure leaves the database
  ahead of the running code; a failed Coolify release is a green GitHub check. No rollback path, no
  post-deploy smoke test, and migrations are never exercised against a real Postgres in PR CI.

## What is genuinely strong

Worth protecting while the above is fixed.

- **The claims/RLS foundation.** `auth.resolve_claims` as the single minter, transaction-scoped
  claims, zero `auth.*` queries per row, `write ⇒ read` implemented once and mirrored in Python, TS
  and SQL. All 48 tables have RLS enabled _and_ forced.
- **The client-portal trust boundary.** `auth.tenant_lineage()` returns empty for a client session in
  SQL — double-belted, explicitly matched so it can never widen, with `can_read_all()` hard-gated
  off. Proven by a real-Postgres test.
- **The template editor.** 4,851 lines of genuinely deep authoring: typed block palette, charts
  bound to real dataset rows through the same resolver the server uses, a visual Jinja table builder,
  dataset-column-aware variable linting whose client and server catalogues are pinned equal by a
  test, per-page geometry that survives to WeasyPrint, and non-destructive version preview/diff.
- **The document render pipeline.** Three-phase worker loop so no pooled connection is held during a
  multi-second render, `FOR UPDATE SKIP LOCKED` with visibility-timeout reclaim, an idempotent
  completion transaction, and dataset snapshots per document.
- **Session handling.** Reactive and proactive refresh with rotation-safe fallback, 60s API leeway,
  and client retry — the best-engineered area in the codebase.
- **The permission registry.** 33 permissions declared once, upserted on startup, code-generated
  into the TS client with a CI drift gate.
- **Queue discipline** in the outbox: skip-locked, exponential backoff capped at an hour,
  `max_attempts`, dead-letter status, and a permanent-error fast path.
- **Two reference-quality patterns worth copying:** `forms.py`'s archive **reference guard** (409
  while any client is mid-fill, with a count) — which templates and datasets both lack; and
  `0021_documents_scope.sql`, which closed a real hole where a `FOR ALL` write policy's USING clause
  OR'd away read scoping.

## Suggested sequence

1. **Security and truth-telling** — register items 1, 2, 3, 10 and the `set_config` escalation test.
   These are the ones that are worse than a missing feature.
2. **Make it usable at all** — 5, 6, 7 (bootstrap roles; a fulfilment surface for staff steps; put
   the flag in compose or delete it).
3. **Stop presenting fiction** — 4, 8, 9, and pattern 7. Either build the real thing or render an
   honest empty state; a demo mode behind a flag is the product call to make here.
4. **See what's happening** — 12, plus dead-letter views and wiring the `LoadErrorBanner` that
   already exists.
5. **Then the long tail** — patterns 3, 4 and 5, which are largely mechanical once the decisions
   above are made.

## Method and caveats

Each domain was traced by an independent pass over the real code, with every claim anchored to
file:line. Findings are **static-analysis-derived**: the SQL-dataset `set_config` escalation in item
1 is reachable in principle and undefended by the prefix guard, but was not exploited against a live
database — it needs a test to confirm or refute. Counts in the scorecard come from the `**Status**`
declarations in the six domain files.

Where this audit contradicts existing documentation, the audit is current. Two known instances:
`CLAUDE.md` lists forgot-password as a remaining follow-up when it is fully built, and `docs/auth.md`
documents the dead `wos-pending` → `/verify` flow as live.
