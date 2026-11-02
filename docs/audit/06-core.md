# 6 · Core & cross-cutting — auth, RLS, email, search, ops, deploy

Portal: **all portals**. Part of the [functional audit](README.md) — statuses: ✅ working · ⚠️ partial · 🎭 demo/mock · ❌ missing · 💥 broken

---

## Core & Cross-Cutting Functional Audit

### Login (WorkOS password auth chain)

- **Status**: ✅
- **What exists today**: `packages/auth/src/auth.ts:80` `passwordAuth` normalises the WorkOS `authenticateWithPassword` outcome into `authenticated | mfa | enroll | verify | misconfigured | failed`, including the `organization_selection_required` → `authenticateWithOrganizationSelection` recovery (`auth.ts:113-137`) using the host-resolved `organizationId`. Each portal's action applies cookies/redirects (`apps/platform/src/routes/login/+page.server.ts:19-54`, identical in client/admin). The cookie-password length pre-check (`auth.ts:75-78`) prevents the historic "bogus invalid code" failure mode, and `misconfigured` returns 503 rather than blaming credentials.
- **Expected**: _As a firm staffer I sign in on my firm's own branded host and land on the dashboard._ _As an operator I get a clear "temporarily unavailable" when the environment is misconfigured, not "wrong password"._
  - Correct credentials → sealed `wos-session` cookie + redirect to `/`.
  - Wrong credentials → 401 with a generic message; no user-existence oracle.
  - Multi-org user → signed into this portal's org deterministically.
  - Missing/short `WORKOS_COOKIE_PASSWORD` → 503 + server log, never a credential error.
  - Login attempts are rate-limited per email/IP.
- **Gaps**:
  - No rate limiting or lockout on the login action anywhere (portal or API) — unlimited password guessing against WorkOS from a single origin.
  - `organizationId` is only present when the host resolves to a registered tenant environment (`apps/platform/src/lib/server/workos.ts:64-70`); on the `$env` fallback a multi-org user gets `failed`, which reads as "wrong password".
  - No e2e coverage of the login chain — `e2e/README.md:24` requires a manually captured `storageState`, so CI exercises only `test_public.py`.

### MFA enrolment & verification (TOTP)

- **Status**: ✅
- **What exists today**: Login-time challenge (`apps/platform/src/routes/mfa/+page.server.ts:52-62`) creates a fresh `challengeFactor` per attempt then exchanges via `authenticateWithTotp`; expired pending tokens are classified (`pendingTokenInvalid`) and bounce to `/login?expired=1` instead of looping on codes. Login-time enrolment (`mfa/enroll/+page.server.ts:36-56`, `:63-134`) returns the QR/secret once and completes login on verify. Settings-side enrolment lives at `account/security/+page.server.ts:20-70`. Cookies are short-lived (`packages/auth/src/cookies.ts:18` — 600s).
- **Expected**: _As a user I can turn on 2FA from settings and am then challenged at every login._ _As a user forced to enrol at login I complete enrolment and sign in in one pass._
  - QR + manual secret both offered; secret shown once.
  - Wrong code → retryable error; expired pending token → restart sign-in.
  - Corrupt/garbled `wos-mfa`/`wos-enroll` cookie → clean restart, not a 500.
  - Recovery/backup codes available if the device is lost.
- **Gaps**:
  - **No recovery codes and no admin MFA reset.** A user who loses their authenticator is permanently locked out — nothing in the admin or firm surfaces resets a factor (`apps/api/app/routers/admin.py`, `firm.py` have no MFA endpoints).
  - No brute-force limit on TOTP code submission.
  - MFA is per-user opt-in only; no per-tenant "require MFA" policy despite this being a regulated-firm product.

### Email verification

- **Status**: 💥
- **What exists today**: `/verify` exists in all three portals and correctly implements `authenticateWithEmailVerification` (`apps/platform/src/routes/verify/+page.server.ts:22-38`) — but it is **unreachable**. Its load guard requires `PENDING_COOKIE` (`verify/+page.server.ts:7`), and no code path sets that cookie: grep for `PENDING_COOKIE` finds only the three `/verify` readers plus the export in `packages/auth/src/cookies.ts:9`. Login's `verify` branch now sends a password-reset link instead (`apps/platform/src/routes/login/+page.server.ts:41-51`) and redirects to `/login?check=1`. `email.verify_email()` (`apps/api/app/email/transport.py:99`) has no call site either.
- **Expected**: _As an unverified user I am told how to verify and can complete it._
  - Exactly one verification mechanism, reachable from login.
  - Verified state reflected in the mirror (`auth.users.enabled` off `email_verified`).
  - Dead routes/templates removed rather than left as decoys.
- **Gaps**:
  - `/verify` (×3 portals) is dead code that always redirects to `/login`; `/login?check=1` has no matching copy path verified in this audit for the "check your email" state on all portals.
  - `email.verify_email()` template is unreferenced dead code.
  - Verification is silently conflated with password-set (`apps/api/app/routers/internal.py:87-89` comment) — a user who already has a password but an unverified email gets a _reset_ link, which is confusing and resets a working credential.
  - `docs/auth.md:41` still documents the `wos-pending` → `/verify` code flow as live.

### Set-password (invite) flow

- **Status**: ⚠️
- **What exists today**: Provisioning mints a WorkOS `reset_password` token and builds `{portal_base}/set-password?token=…` (`apps/api/app/provisioning.py:261-271` for invite, `:466-477` for resend), emails it via Resend, and returns `invitation_url` in the API response. The portal page validates + confirms via `confirmPasswordReset` (`apps/platform/src/routes/set-password/+page.server.ts:12-28`; `packages/auth/src/auth.ts:41-56`), classifying `invalid` vs `weak`.
- **Expected**: _As an invited user I click the emailed link, set a password, and sign in._ _As an ops user I can copy the link if email fails._
  - Token single-use and short-lived; reuse shows "expired — request a new one".
  - Weak/breached passwords rejected with a helpful message.
  - Resend-invite mints a fresh token and invalidates the old one.
  - The link is never persisted or logged.
- **Gaps**:
  - **Token expiry/single-use is entirely WorkOS's and unasserted.** We mint nothing ourselves, so there is no test pinning the TTL or single-use property, and no way to shorten it.
  - `invitation_url` — a live credential — is returned in an HTTP response body and rendered in the admin/platform UI (`apps/admin/src/routes/tenants/[id]/members/+page.server.ts:54`, `apps/platform/src/routes/team/+page.server.ts:80`). It will land in proxy logs, browser history and screenshots.
  - `validateNewPassword` (`packages/auth/src/validation.ts`) is a length check only; strength enforcement is delegated to WorkOS and surfaced as a generic "choose a stronger password".
  - Resend-invite (`provisioning.py:466`) does not revoke the previous token.

### Forgot-password

- **Status**: ⚠️ (CLAUDE.md is stale — this is **built**, not a follow-up)
- **What exists today**: A real, wired chain. UI form → `apps/platform/src/routes/forgot-password/+page.server.ts:5-18` (identical in client/admin) → `requestPasswordReset` (`packages/auth/src/environment.ts:129-146`) → `POST /internal/forgot-password` (`apps/api/app/routers/internal.py:80-107`), which resolves the host's WorkOS project (falling back to the core project for the ops host), calls `reset_password`, and sends `password_reset_email` via Resend. Always returns `{ok: true}` and the UI always says "sent", so there is no account-existence oracle.
- **Expected**: _As a user who forgot my password I request a link and receive one._
  - Same response whether or not the account exists.
  - Per-email and per-IP throttling.
  - Sender is the firm's domain on a white-label host.
  - Failures are observable to operators.
- **Gaps**:
  - **CLAUDE.md's "State & next steps" says "Forgot-password + email-verification via Resend are the remaining follow-ups"** — false for forgot-password, and misleading for verification (see above). Doc rot on the most load-bearing orientation file.
  - **No throttling.** The public form is unauthenticated and unlimited → email-bombing any known address via our own Resend account and WorkOS quota.
  - `internal.py:102` calls `send_email(...)` **without** `sender=sender_for(tenant_id)` — unlike the invite paths (`provisioning.py:269`), so reset emails always come from the Graphene sender even on a white-labelled host.
  - The blanket `except Exception` (`internal.py:103-105`) swallows Resend/WorkOS failures into an INFO log; with no error tracking, a total reset outage is invisible.
  - `INTERNAL_API_TOKEN` is absent from every portal `.env.example` — a fresh dev setup gets a silently no-op forgot-password form.

### Logout

- **Status**: ✅
- **What exists today**: `apps/platform/src/routes/logout/+page.server.ts:5-25` loads the sealed session, obtains WorkOS's `getLogoutUrl()`, deletes `wos-session`, and redirects; an unreadable session still clears locally.
- **Expected**: _As a user, signing out ends the session everywhere._
  - Local cookie cleared unconditionally.
  - WorkOS session revoked (not just the cookie dropped).
  - Failure to reach WorkOS still logs out locally.
  - Logout is POST-only (no CSRF logout).
- **Gaps**: Only reachable as a form action (good), but there is no confirmation that the WorkOS sign-out redirect is configured per tenant project — `docs/auth.md:39` notes `getLogoutUrl()` errors without one, and the `catch` silently degrades to local-only logout, leaving the WorkOS session live.

### Session refresh & mid-session expiry

- **Status**: ✅
- **What exists today**: `packages/auth/src/session.ts:71-107` implements reactive (expired) and proactive (within 60s of `exp`) refresh; a _proactive_ refresh failure falls back to the still-valid token (refresh-token-rotation safe), while a _reactive_ failure sets `clearCookie` so the dead cookie stops being retried. `hooks.server.ts` persists the rotated sealed session (`apps/client/src/hooks.server.ts:66-67`). The API adds a 60s `leeway` on `exp` (`apps/api/app/auth.py:135`) to cover the boundary, and `$lib/api.ts:28` retries 401/5xx/network with backoff so a refresh-boundary blip surfaces as a retry, not empty data.
- **Expected**: _As a user working for hours I am never bounced to login mid-task by a token boundary._
  - Near-expiry tokens refresh before being handed to the API.
  - A revoked refresh token clears the cookie once, then redirects to login.
  - Transient 401s do not render as "no data".
  - Behaviour identical across the three portals.
- **Gaps**:
  - On expiry the user is redirected to `/login` with **no return-to**: every page load does `if (!locals.user) redirect(303, '/login')` with no `?next=`, so an expiring session loses the user's place and any in-progress form.
  - `atob` is used to decode the JWT payload (`session.ts:51`) — fine on Node 24, but no test covers a malformed token there beyond the try/catch.
  - Refresh happens per-request per-portal with no lock; two concurrent requests can both attempt a rotation (mitigated, not eliminated, by the proactive-failure fallback).

### Security settings page (change password, 2FA toggle)

- **Status**: 🎭 (platform + admin) / ✅ (client)
- **What exists today**: 2FA enable/disable is real in all three portals, with password re-authentication before removing a factor (`apps/platform/src/routes/account/security/+page.server.ts:71-104`). **Change password is a fake in two of three portals**: `apps/platform/src/routes/account/security/+page.server.ts:106` and `apps/admin/src/routes/account/security/+page.server.ts:106` both carry `// Demo only — password changes aren't wired to WorkOS yet.` and return `{ passwordChanged: true }` after validation only. Only the client portal actually calls `updateUser` with a prior re-auth (`apps/client/src/routes/account/security/+page.server.ts:106-144`).
- **Expected**: _As a staff member I change my password from settings and the old one stops working._
  - Current password re-verified before the change.
  - New password rejected if weak/breached, with a clear message.
  - Success message only after the provider confirms.
  - Other sessions optionally revoked.
- **Gaps**:
  - **Staff and Graphene ops users are told their password changed when it did not.** This is the single worst trust defect found: a security control that reports success while doing nothing. The correct implementation already exists 40 lines away in the client portal.
  - No session revocation after a password change in any portal.
  - Disabling 2FA deletes _all_ TOTP factors (`security/+page.server.ts:88-95`) with no re-confirmation and no audit event in `audit.log` (WorkOS-side change, invisible to our trail).

### Permission registry vs actual UI gates

- **Status**: ⚠️
- **What exists today**: `apps/api/app/permissions.py` is a genuine single source of truth: 33 permissions declared once, upserted on startup (`main.py:48-50`), consumed as typed constants by `require_permission`, and code-generated into `@graphene/api-client/permissions` with CI drift-failure (`.github/workflows/pr.yml:75-79`). Admin gates per-endpoint and resolves the required permission from the _target_ tenant (`admin.py:61-89`). Portals gate nav and page loads via `can` (`apps/admin/src/lib/permissions.ts`, `apps/platform/src/routes/+layout.svelte:73-97`).
- **Expected**: _As a firm owner I grant a role and exactly the matching UI appears._ _As a scoped user I never see a nav item that 403s._
  - Every registry permission gates at least one endpoint.
  - Every nav item and action is gated on its backing permission.
  - No endpoint requires a permission no role can hold.
  - Page loads degrade to an empty/unauthorised state, never a raw 403.
- **Gaps**:
  - **Every permission-gated endpoint is unreachable on a fresh install.** `0001_auth_core.sql:631` seeds the model tenant **blank** — no `auth.roles`, no `auth.role_permissions` anywhere in migrations or deltas. `clone_model_roles` (`provisioning.py:58-110`) therefore clones nothing, so a newly provisioned firm has zero roles and zero permissions. The only ways in are `SYSTEM_USER_IDS` break-glass or hand-building roles in the admin `/model` UI. There is no documented bootstrap for this.
  - **403 traps / ghost nav in the platform portal**: `Documents`, `Templates`, `Forms`, `Clients` and `Dashboard` are rendered **ungated** (`apps/platform/src/routes/+layout.svelte:69-83`) while every backing endpoint requires `documents:read` / `templates:read` / `forms:read`. The page loads degrade to empty (`documents/+page.server.ts:19-26`), so the user gets a permanently blank section with no explanation — a silent dead-end rather than an honest "not authorised".
  - **Nav/page gate mismatch**: nav shows _Roles_ only with `roles:write` (`+layout.svelte:99`) but the page needs only `roles:read` (`team/roles/+page.server.ts:8`) — a `roles:read` holder has a working, invisible page.
  - **`contacts:read` gates nothing on its own.** `crm.py:9` deliberately has no `require_permission` (RLS is the gate); the permission only appears inside `require_any_permission` in `catalog.py:60,91`. It is effectively decorative while the UI treats it as meaningful.
  - `/catalog/models` and `/catalog/fee-packages` require `contacts:read|cases:read|flows:read` but have **no UI consumer** — unreachable features.
  - `applications.py`'s five `cases:*`-gated endpoints 404 unless `FLOWS_ORCHESTRATOR_V2=1` (`applications.py:34`), which is not settable in `docker-compose.yaml` — so `cases:read`/`cases:write` are grantable but inert in production.
  - Stale comment: `permissions.py:181-182` says "only the read endpoints exist today… DATASETS_WRITE is declared for the taxonomy" — false, four `datasets:write` endpoints exist (`datasets.py:393,453,492,510`).

### Claims contract & privilege model

- **Status**: ✅
- **What exists today**: `auth.resolve_claims` is the single minter, redefined forward through `0001 → 0002 → 0004 → 0027`, final body at `0027_tasks.sql:128-219`, yielding `user_uuid`, `tenant_id`, `tenant_ids`, `portal`, `contact_ids`, `account_ids`, `role_ids`. `role_ids` is scoped to _active_ memberships for the _resolved portal_ (`0027_tasks.sql:200-205`) and read by `auth.role_ids()` for role-assigned task filtering (`app/routers/tasks.py:158`). `write ⇒ read` and the `system` bypass are implemented once in `permissions.satisfies` (`permissions.py:255-263`) and mirrored in TS `can`, tested at `tests/test_permissions.py:14`. `is_system` is triple-sourced — `SYSTEM_USER_IDS` allowlist, `role in ('system','admin')`, or the `system` permission (`app/auth.py:170-178`), mirrored in SQL by `auth.is_system()` (`0001_auth_core.sql:460`).
- **Expected**: _As the platform, a request's authority is derived once, in the DB, from a verified token._
  - Claims minted once per request, transaction-scoped (`set_config(..., true)`).
  - Fail-closed when the principal doesn't resolve.
  - `write` implies `read` identically in Python, TS and SQL.
  - Break-glass is auditable and time-boxed.
- **Gaps**:
  - **`auth.is_system()` in SQL does _not_ honour `SYSTEM_USER_IDS`** (`0001_auth_core.sql:460` checks only role/permission), while Python `is_system` does (`auth.py:172`). A break-glass principal therefore passes application gates and `admin_session` but is _not_ a superuser under RLS — a silent split-brain that will read as "the admin UI works but shows nothing".
  - `SYSTEM_USER_IDS` has no expiry, no audit event on use, and no warning log — unlike the dev bypass which logs loudly (`auth.py:96-100`).
  - The dev auth bypass accepts an unverified `dev:<b64>` principal with arbitrary `permissions` (`auth.py:80-104`). It is double-guarded (flag + `app_stage != 'production'`), but `APP_STAGE` defaults to `production` in `config.py:22` and to `production` in compose — correct — while `apps/api/.env.example:9` sets `APP_STAGE=dev`, so a copied env file on a staging box is one env var from a full auth bypass.
  - `role` is taken from the WorkOS token and `auth.is_system()` trusts `role in ('system','admin')` — any WorkOS environment that names a role `admin` grants cross-tenant superuser.

### RLS coverage & FORCE

- **Status**: ⚠️
- **What exists today**: Every one of the 48 tables created across `0001`–`0029` has `ENABLE ROW LEVEL SECURITY`, and all are forced — explicitly, or via the per-schema `FORCE` loops (`0001_auth_core.sql:657-666` for `auth`, `0002_platform_crm.sql:579-588` for `crm`+`platform`, `0021_flows.sql:203-211` for `flows`). I found **no tenant-scoped table missing RLS or FORCE**. `documents.upload_requests` was dropped in `0008`. `flows.outbox` is deliberately deny-all for `authenticated` (`0025_flows_orchestration.sql:117`). `0014_audit_append_scope.sql` fixed a real forgery hole (`WITH CHECK (true)` on `documents.audit_log`).
- **Expected**: _As a tenant, no query from another tenant's session can ever see my rows._
  - Every tenant-scoped table: `ENABLE` + `FORCE` + a policy, in the same migration.
  - Owner (migrator) is bound too.
  - Absent claims see nothing.
  - Secrets tables have no `authenticated` grant at all.
- **Gaps**:
  - **`auth.tenants` reads with `USING (true)`** (`0001_auth_core.sql:577`). Any authenticated principal — including a _client end-user of one firm_ — can enumerate every firm on the instance: `name`, `alias`, `uid`, `website`, `workos_project_id`, `workos_organisation_id` and the whole `theme` jsonb (every competitor firm's white-label branding). The recursion rationale in the comment is sound; the mitigation (a column-restricted view, or a recursion-safe scoped policy) was never built.
  - `auth.permissions` and `auth.role_permissions` also read `USING (true)` (`0001_auth_core.sql:606,611`) — the second leaks the grant graph, since the row carries no tenant column.
  - **`auth.api_keys` grants `INSERT/UPDATE/DELETE` to `authenticated`** with only a tenant-scope policy (`0001_auth_core.sql:594-596`) — no permission gate. No endpoint exposes it today, so it is latent, but any future tenant-session query can mint keys.
  - The `FORCE` loops are `pg_tables`-wide per schema, so a future table added to `auth`/`crm`/`platform`/`flows` in a _later_ migration is **not** covered — the loops only ran once. `0026_kyc_checks.sql:55` and `0027_tasks.sql:114` correctly force explicitly; this is convention, not enforcement. There is no test asserting "every table in these schemas has relforcerowsecurity".

### Book-scoping consistency

- **Status**: ⚠️
- **What exists today**: The `can_read_all()`-or-`contact_scope()` pattern is applied to `crm.contacts` (`0002:457-461`), `crm.contact_notes`, `crm.relationships`, `platform.accounts`, `platform.transactions`, `documents.documents` (`0021_documents_scope.sql:33-51`, which also closed a real hole where `documents_write`'s permissive `FOR ALL` USING clause OR'd away the read scoping), `documents.audit_log`/`requests`/`request_items` (`0022_documents_activity_scope.sql`), and `tasks.tasks` (`0028_tasks_polish.sql:30-53`).
- **Expected**: _As a book-scoped adviser I see only my own clients' records, consistently, everywhere._
  - Any table carrying a `contact_id`/`account_id` is book-scoped, not merely tenant-scoped.
  - A `FOR ALL` write policy never widens read visibility.
  - The scoping expression is one shared idiom, not per-table improvisation.
  - A test proves the boundary per table.
- **Gaps**:
  - **`flows.cases` and `flows.case_steps` are tenant-wide only** (`0021_flows.sql:151-171`): a book-scoped adviser sees every client's onboarding case in the firm, including step detail — despite the _documents_ attached to those cases being book-scoped. Same for `flows.executions` and `flows.domain_events` (`0025:106-113`).
  - **`crm.kyc_checks` is tenant-wide** (`0026_kyc_checks.sql:43-48`) — identity-verification outcomes for every client, visible to any staffer. This is the most sensitive table in the schema.
  - **`documents.shares` is tenant-wide with full write** (`0017_document_shares.sql:37-41`): a book-scoped adviser can list _and revoke_ public share links for documents they cannot see.
  - `documents.attachments` and `platform.instructions` are tenant-wide for staff (`0018:32-38`, `0016:36-41`).
  - `crm.applications` scopes on `adviser_id = auth.uid()` rather than `contact_scope()` (`0002:515-518`) — a fourth, inconsistent idiom.
  - `tasks_write`'s `WITH CHECK` is tenant-wide (`0028:51-53`), so a book-scoped staffer can create a task assigned to a contact outside their book; only application code prevents it.

### Portal trust boundary (client portal)

- **Status**: ✅
- **What exists today**: The safety lever is real and double-belted: `auth.tenant_lineage()` returns `'{}'` when `auth.portal() = 'client'` **or** the legacy `auth.role() = 'client'` (`0004_portal_scope.sql:28-36`), so a client session fails every `tenant_id = ANY(auth.tenant_lineage())` staff policy automatically and is reachable only via the explicit `contact_scope()`/`account_scope()` branches. `can_read_all()` is additionally hard-gated off the client portal (`0004:43-45`) so the system bypass cannot leak in. `portal` is server-derived by matching the active org against `tenant_environments.client_portal_org_id` (`0027:154-165`), defaulting to `platform` — and the client branch is matched _explicitly_, so it can never be widened by an unknown org. Client contact scope in the client portal is `crm.contact_users` only, never an adviser book (`0027:171-176`).
- **Expected**: _As a client I can never see another client, and never anything firm-internal._
  - Empty lineage in the client portal, enforced in SQL not application code.
  - `contacts:read_all` inert in a client session.
  - Notes/relationships/kyc staff-only.
  - Proven by a real-Postgres test.
- **Gaps**:
  - Proven for CRM (`tests/test_rls_crm.py:238` `test_client_portal_is_a_hard_boundary`) and documents, but **not** for `flows.cases`, `tasks.tasks` (RLS-level), `crm.kyc_checks` or `platform.instructions` — each of which has a client-visible branch keyed on `contact_scope()`/`account_scope()` with no isolation test.
  - `auth.tenants USING (true)` punches straight through this boundary: a client can enumerate every firm on the instance (see above).

### Isolation test coverage

- **Status**: ⚠️
- **What exists today**: Seven dedicated real-Postgres (testcontainers) RLS suites. `tests/test_rls.py` (claims minting, staff/client scope, api_keys isolation, fail-closed empty claims, audit append-only, per-tenant email uniqueness). `tests/test_rls_crm.py` (read-all vs book-scoped vs client personas, notes staff-only, cross-tenant invisibility, the hard client boundary, `platform_links`). `tests/test_documents_rls.py` (852 lines — book scoping, activity/request scoping, cross-tenant audit forgery at `:490`, client request-item visibility, append-only). Plus `test_forms_rls.py`, `test_form_responses_rls.py`, `test_templates_rls.py`, `test_datasets_rls.py` (each: staff-only, fail-closed, cross-tenant write rejected, per-tenant uniqueness). `tests/test_tasks_api.py:309` covers cross-tenant task invisibility at the API level.
- **Expected**: _As a reviewer I can point to a test for every tenant-scoped table's boundary._
  - One isolation test per tenant-scoped table.
  - Fail-closed (empty claims) asserted per table.
  - Cross-tenant _write_ rejection asserted, not just read.
  - The suite cannot silently skip in CI.
- **Gaps**:
  - **Zero RLS tests** for `flows.*` (flows, flow_versions, cases, case_steps, executions, domain_events, outbox), `crm.kyc_checks`, `documents.shares`, `documents.attachments`, `documents.render_jobs`, `platform.instructions`. `tasks.tasks` is covered only through the API, not at the policy level.
  - **The whole DB-backed suite skips (not fails) when Docker is absent** (`tests/conftest.py:44,55`). Nothing asserts it actually ran, so a runner without Docker produces a green `moon run :test` with the isolation proof silently absent.
  - No test asserts the _structural_ invariant — "every table in `auth|crm|platform|flows|documents|tasks` has `relrowsecurity` and `relforcerowsecurity`" — which is what would catch the next table added outside a `FORCE` loop.

### Transactional email & notifications

> **UPDATE (2026-08-03) — largely addressed for the template/branding half.** `app/email/transport.py` now
> carries seven **tenant-branded** templates (`brand_for`/`resolve_sender` are real, not stubs), and a
> firm-editable **email-template system** has landed on top: a dedicated `email.*` schema
> (`email.templates`/`template_versions`, migration `0042`, draft/publish/versioned), an
> `email_templates:*`-gated CRUD/versions/publish router, a render seam (`app/email/document.py`,
> `css-inline`) + `app/email/render.py::render_or_default`, and a platform editor (`/email-templates`
> list + edit). **All five send sites** (invite, password-reset, document-sent, document-share,
> request-raised) now use a firm's published template when set, else the built-in default
> (fail-safe) — so the last gap below (hardcoded "Graphene" wordmark, no tenant branding) is closed,
> while `verify_email` remains the caller-less dead code the verification finding above names. **Where the defaults actually live matters for reading this
> register:** they are composed in **Python** (`app/email/transport.py` — `shell_html` plus the
> `masthead`/`cta_block`/`footer_for` builders and one builder per code), and
> `app/email/defaults.py` is a second authoring of the same copy used to
> prefill the editor. That second copy is **pinned** now, not merely documented:
> `tests/test_email_send_parity.py` compares the two authorings' rendered subject and visible copy per
> code, on a full send and on a sparse one, which found six real divergences. (A THIRD authoring in
> `pkgs/email` — Svelte mirrors of three codes behind a dev-only `/dev/emails` route — was deleted with
> that package in Aug 2026; it was off the send path entirely and nothing pinned it against either.)
> Full arrangement, including the two style shapes a stored body can have, in
> [`../api.md`](../api.md) → "Email". The **task notifier** has
> since moved onto the same path (`render_or_default(code="task_notice")` over a firm-branded
> `email.task_notice_email` default; the unbranded `flows/notifications.py::build_notification` shell
> it used is deleted), so the notifications gap below now narrows to one thing: **`notify.*` still has
> no producer**, and its payloads are still built inline at enqueue rather than resolved through a
> firm's template. The snapshot below is the pre-change audit and is kept for history.

> **UPDATE (2026-08-04) — the override path grew a second author, a fail-safe and an accountability
> record.** Three things the 08-03 note above does not describe. **(1) Operations can author a firm's
> emails.** The admin surface moved from a model-only page to the per-tenant
> `/tenants/{uid}/email-templates` (list · edit · preview, beside Branding and Terminology; `/model`
> deep-links to it and the old path 308s), and its permission is chosen by the TARGET tenant the way
> the shared member/role endpoints already were — `model:*` for the preset, `tenants:*` for a firm
> (`admin.py::_require_tenant_scope`). One service (`app/email/templates_service.py`) holds the SQL for
> both doors, so the publish gate cannot differ between them; the router's copy of that gate is gone.
> **(2) A version records which SIDE wrote it** — `email.template_versions.authored_by_scope`
> (`0051`), nullable with no default so pre-existing rows read as unknown rather than being back-dated
> to `'firm'`, sticky at `'operations'` so a later firm save cannot launder the fact away. This closes
> a real gap in this register's terms: `created_by` resolves to a person's NAME, so before 0051 an
> operator editing a firm's template on their behalf was indistinguishable from the firm's own staff
> doing it — a governance concern, not a cosmetic one, and the firm's version history now badges it.
> **(3) The fail-safe is per SEND, not per publish.** `render_published_email` re-checks the body for
> renderable content AFTER the Jinja merge and reports an empty result as absent, so the caller falls
> back to the shipped default. Publish alone could not cover a version published before that gate
> existed, or copy that is legitimately publishable yet merges to nothing for one send. **Unchanged:**
> `notify.*` still has no producer and is still built inline at enqueue.
>
> **Update (04 Aug 2026) — the "`sender_for` is a no-op" gap below is closed, and it was worse than
> "a stub": `sender_for` had become a real read of `resolve_sender`'s 300s cache, which is exactly
> what made it dangerous. A miss returns the Graphene `EMAIL_FROM` silently and it can never yield a
> `Reply-To`, so every path that read it was cosmetically white-labelled — the invite paths warmed
> the BRAND cache and then read the SENDER one (nothing had filled it), and the two worker-side paths
> (the outbox dispatcher, `tasks/notifier.py`) run in a separate process whose `_sender_cache` is a
> different, permanently empty dict, so their miss was total rather than occasional. Every send path
> now resolves (`resolve_sender`, with the `Reply-To`) or reads the identity the producer resolved
> into the outbox payload; `tests/test_email_sender_identity.py` holds the caches cold to prove it.
> `internal.py`'s reset path was already fixed at the 30 July re-audit. Still true: per-firm VERIFIED
> sender domains do not exist, so the `From` remains our verified address wearing the firm's display
> name unless the firm's support address happens to sit on that same domain.

- **Status**: ⚠️
- **What exists today**: One sender (`app/email/transport.py:29 send_email`) via Resend, best-effort (returns `False` and logs, never raises). Three templates, four live producers: **invite** (`provisioning.py:266`, `:472` resend-invite), **password reset / "verification"** (`internal.py:101`), **task notices** — assign-time + near-due reminders with `notified_at`/`due_soon_notified_at` idempotency stamps and role/user/contact recipient resolution (`app/tasks/notifier.py`), and the **outbox** `notify.*`/`task.requested` router (`app/flows/dispatcher.py:148`). `sender_for(tenant_id)` (`email.py:20`) is a stub returning `settings.email_from`.
- **Expected**: _As a firm I send branded, deliverable email from my own domain._ _As an operator I can see what failed to send._
  - Every user-visible state change has a matching email or a deliberate decision not to.
  - Per-firm verified sender domains.
  - Delivery failures observable and retryable.
  - No email path silently disabled by missing config.
- **Gaps**:
  - **`notify.*` has no producer.** The dispatcher routes it (`dispatcher.py:148`) and `notifications._send` exists, but the only `notifications.enqueue` call in the codebase (`app/flows/orchestrator.py:371`) emits `task.requested` — and only under `FLOWS_ORCHESTRATOR_V2`. So flow-driven client/staff notifications are unreachable in production.
  - **`sender_for` is a no-op**, so white-label is cosmetically complete (branding, hosts, logos) but every email still comes from `no-reply@grapheneplatforms.com` — and `internal.py:102` does not even pass `sender_for`.
  - **Missing emails**: document sent/shared to a client, document request raised, request due/overdue, share link expiring, case/onboarding step assigned to a client, account deactivated, password _changed_ confirmation, new-device sign-in.
  - `RESEND_API_KEY` blank ⇒ every send logs a warning and returns `False` (`email.py:32-34`); `worker.py:341` skips the notifier scan entirely. So on a deploy without Resend, task notification is silently off with no health signal.
  - `RESEND_API_KEY`/`EMAIL_FROM`/`OPERATIONS_PORTAL_URL` are absent from `apps/api/.env.example`.
  - Email templates are inline-styled Graphene-branded HTML with a hardcoded "Graphene" wordmark (`email.py:63-64`) — no tenant branding, despite `auth.tenants.theme` carrying a logo.

### Search (all three portals)

- **Status**: 🎭
- **What exists today**: All three search bars are fabrications, and **there is no search endpoint in the API** (no `/search` route in any of the 17 routers; grep for `search` in `app/routers/` returns only two regexes inside the dataset SQL parser). `apps/admin/src/lib/SearchBar.svelte:9-15` hardcodes five fictional tenants ("Acme Advisers", "Globex Wealth", "Initech Financial", "Stark Capital", "Wayne Private") and navigates to `/tenants` regardless of selection. `apps/platform/src/lib/SearchBar.svelte:7-12` hardcodes four fictional clients ("Margaret Cole", "David Osei", "Priya Nair", "Tom Fletcher") and `select()` always goes to `/clients` (`:36`). `apps/client/src/lib/SearchBar.svelte:11` searches `$lib/demo` fixtures and pairs them with a fake "AI-lite" assistant (`$lib/assistant.ts`) plus four canned prompts.
- **Expected**: _As a staffer I press ⌘K, type a client's name, and jump straight to them._
  - Results come from the API, RLS/book-scoped.
  - Cross-entity (clients, documents, tasks, cases) with typed grouping.
  - Selecting a result navigates to that entity, not its list page.
  - Empty state says "no matches", not fictional matches.
- **Gaps**:
  - **This is the most user-visible fabrication in the product.** A signed-in adviser at a real firm types their own client's name and sees "Margaret Cole" — another firm's-looking name that does not exist — then lands on an unrelated list page. In a regulated multi-tenant product this reads as a cross-tenant data leak to the user.
  - No search endpoint, no search index, no `pg_trgm`/tsvector columns anywhere in `0001`–`0029`.
  - The admin bar's fictional tenant names are indistinguishable from real tenants to an ops user.
  - `$lib/assistant.ts` implies an AI capability that does not exist.

### Background machinery (worker, outbox, notifier, poller)

- **Status**: ⚠️
- **What exists today**: One `render` service (`docker-compose.yaml:88-113`, `Dockerfile.worker` → `CMD python -m app.worker`) hosts **three** loops in one process (`app/worker.py:320-355`): the `documents.render_jobs` drain, `dispatcher.dispatch_once` for `flows.outbox`, and the throttled task-notifier scan. Each is individually try/except-wrapped so one failure can't kill the loop. The outbox has proper queue discipline — `FOR UPDATE SKIP LOCKED`, exponential backoff capped at 1h, `max_attempts` 8, `dead_letter` status, and a `PermanentDispatchError` fast-path (`dispatcher.py:124-185`, `notifications.py:25-31`). The WorkOS poller runs **in the API process** (`main.py:54`), no-op at `WORKOS_SYNC_POLL_SECONDS=0`.
- **Expected**: _As an operator I can see queue depth, failure rate and the dead-letter list, and be paged when a queue stalls._
  - Each queue has a healthcheck and a depth metric.
  - Dead-lettered rows are visible and replayable in a UI.
  - The worker is scaled/monitored independently of the API.
  - A stalled worker surfaces to users, not as a spinner.
- **Gaps**:
  - **The `render` service has no healthcheck** (unlike `api`/`client`/`platform`/`admin`, which all have one). A crash-looping or wedged worker is invisible; Coolify sees a running container.
  - **Dead-lettered outbox rows are invisible.** `flows.outbox` is deny-all for `authenticated` (correct) and **no endpoint or UI reads it** — grep for `outbox` across `app/routers/` and all three portals returns only a doc comment. Same for `documents.render_jobs` failures. Recovery requires psql.
  - **No metrics of any kind.** No Prometheus, no OpenTelemetry, no structured logging — `logging.basicConfig` with a text format (`worker.py:359`). Queue depth, age of oldest queued job, dead-letter count: unobservable.
  - **The API-hosted poller does not survive replication.** `main.py:54` starts it per API process, so N replicas poll WorkOS N times with no leader election, racing on the same cursor.
  - `WORKOS_SYNC_POLL_SECONDS` defaults to `0` in both `config.py:130` and `docker-compose.yaml:56` ⇒ **the WorkOS→mirror reconcile is off in production by default**, and the legacy startup reconcile is disabled (`main.py:38-44`). With no webhook consumer either, the mirror drifts silently.
  - **`FLOWS_ORCHESTRATOR_V2` is not in `docker-compose.yaml` at all** — the flag exists (`config.py:33`, default `False`), the UI tells the user to set it (`journeys/+page.svelte:122`), but there is no way to set it on the deployed stack without editing compose.
  - Local dev has **no worker task**: `apps/api/moon.yml` defines `dev`/`lint`/`typecheck`/`test`/`migrate`/`generate-permissions` but nothing runs `app.worker`. So `moon run :dev` gives a stack where generated documents never render and the outbox never drains — a footgun for every new developer.

### Healthchecks & error tracking

- **Status**: ❌
- **What exists today**: `GET /health` returns a static `{"status":"ok"}` (`app/routers/health.py:11-13`) — no DB, storage or WorkOS probe. Compose healthchecks exist for `api`, `client`, `platform`, `admin` (the portals just fetch `/`). **No Sentry, no OpenTelemetry, no Prometheus, no structured logging** anywhere — grep for `sentry|opentelemetry|prometheus|metrics` across `apps/api/app`, all three portals and `packages/` returns nothing.
- **Expected**: _As an operator I learn about a failure from an alert, not a user._
  - `/health` distinguishes liveness from readiness (DB + storage reachable).
  - Unhandled exceptions reach an error tracker with request context.
  - Structured JSON logs with a correlation id.
  - Alerts on error rate, queue depth and 5xx.
- **Gaps**:
  - `/health` is a liveness-only stub: the API reports healthy with an unreachable database, so compose's `depends_on: service_healthy` gates the portals on nothing meaningful.
  - **Zero error tracking.** Every one of the many `except Exception: logger.exception(...)` sites — the outbox dispatcher, the notifier, the render worker, the poller, `internal.py`'s deliberately-silent forgot-password swallow — writes to container stdout and nowhere else.
  - No correlation id threading portal → API → worker, so a user-reported failure cannot be traced.
  - No alerting configuration in `infrastructure/` or compose.

### Rate limiting & abuse controls

- **Status**: ❌
- **What exists today**: Essentially nothing. Two mechanisms were _designed_: `flows.public_start_throttle` (`0024_flows_p0_hardening.sql:47-55`, a per-tenant fixed-window table) and `settings.public_onboarding_max_per_hour` (`config.py:40`). **Neither is read or written by any code** — grep across `app/` finds the setting only in its own comment and the table only in the migration. And the endpoint they were built for, `POST /public/onboarding/start`, **does not exist** in any router.
- **Expected**: _As the platform I bound abuse on every unauthenticated surface._
  - Login, forgot-password and public share resolution are rate-limited.
  - The public onboarding entry is capped per tenant per hour.
  - Limits are observable (counter + 429 rate).
  - Share tokens resist enumeration.
- **Gaps**:
  - **The entire 0024 "H1 public throttle" hardening is dead infrastructure** — table, RLS, grants and a settings knob, wired to nothing. Anyone reading the migration or CLAUDE.md would reasonably conclude the public path is throttled.
  - **`POST /forgot-password` is unauthenticated and unlimited** on all three portals → email bombing plus Resend/WorkOS quota exhaustion.
  - **`POST /login` is unauthenticated and unlimited** → password spraying; no lockout.
  - **`GET /share/{token}` and `/share/{token}/content` are unauthenticated and unlimited** (`shares.py:231,313`). Tokens are `secrets.token_urlsafe(24)` (192 bits — fine), but there is no throttle, so enumeration attempts are free and invisible, and `access_count` is the only signal.
  - `/internal/*` relies solely on a shared bearer token (constant-time compared, 404 on failure — good) with no rate limit and no IP allowlist in code; the "internal network only" property is a deployment convention documented in `internal.py:1-9`, not enforced.

### CORS, upload limits & secret transport

- **Status**: ⚠️
- **What exists today**: CORS is `allow_credentials=True` with `allow_methods=["*"]`, `allow_headers=["*"]`, a static origin list plus `allow_origin_regex` for tenant hosts (`main.py:63-71`); compose feeds the static list off Coolify's `SERVICE_URL_*` (`docker-compose.yaml:30`). Upload caps are real and enforced twice: 25 MiB for documents (`storage.py:143`, checked at `files.py:99-104` and `documents.py:198-206`) and 512 KiB for branding assets (`storage.py:49,117`). Portals derive origin from `x-forwarded-proto`/`x-forwarded-host` so SvelteKit CSRF works across many tenant hosts.
- **Expected**: _As the platform I accept cross-origin credentialed requests only from origins I own._
  - Origin regex is anchored and cannot match an attacker domain.
  - Methods/headers narrowed to what is used.
  - Upload size and content-type validated server-side before storage.
  - Secrets never travel in a response body.
- **Gaps**:
  - `ALLOWED_ORIGIN_REGEX` is operator-supplied and passed straight to Starlette with no anchoring or validation (`config.py:157`, `main.py:66`). An unanchored pattern (the compose example `^https://(client|platform|admin)\.dev\.fra\.corp\..+$` is anchored, but nothing enforces that) plus `allow_credentials=True` is a full cross-origin session-theft primitive.
  - `allow_methods=["*"]` / `allow_headers=["*"]` with credentials is broader than needed.
  - **`GET /internal/env-config` returns the tenant's decrypted `workos_api_key` and `cookie_password` in a JSON body** (`internal.py:46-56` → `environments.env_config_by_host`), over plain HTTP inside the compose network (`API_URL: http://api:8000`). Correct by design given the master-key-holder split, but it means every portal process holds every tenant's WorkOS API key in memory and cache (`environment.ts:51`, 60s TTL), and one compromised portal container yields all tenants' identity credentials.
  - `_reject_oversize` only checks `file.size` when the client supplies it (`files.py:100`) — a chunked upload without `content-length` bypasses the pre-check and is caught only later in `storage.py:173`, after buffering.

### Deploy pipeline & migrations

- **Status**: ⚠️
- **What exists today**: `.github/workflows/deploy.yml` on push to `main`: checkout → toolchain → install → **`moon run api:migrate`** with `MIGRATION_DATABASE_URL` → `moon run :build` → `curl -fsSL -X POST "$COOLIFY_DEPLOY_WEBHOOK"`. Runs on a self-hosted Proxmox runner so the DB is never exposed. `concurrency: deploy-main, cancel-in-progress: false`. Migrations are SQL-first, expand/contract by convention, with `SET LOCAL lock_timeout` (`0024:17`). PR CI is strong: lint, format, `:check :typecheck`, test, build, gitleaks over full history, Trivy CRITICAL/HIGH, and OpenAPI + permissions drift gates.
- **Expected**: _As an operator, a deploy either lands fully or leaves the system in its prior working state._
  - Migrations are backwards-compatible with the running app.
  - A failed build never leaves the DB ahead of the code.
  - The pipeline waits for and verifies the deploy result.
  - Rollback is documented and rehearsed.
- **Gaps**:
  - **Migrations run before the build.** If `moon run :build` fails (step 3), the database is already migrated and the old app keeps running against a newer schema. Expand/contract makes this survivable in principle but it is unverified and unrehearsed.
  - **The deploy is fire-and-forget**: `curl -X POST` returns on webhook acceptance, not on deploy success. A failed Coolify deploy is a green GitHub check.
  - **No rollback path.** No down-migrations exercised, no documented procedure, no smoke test after release.
  - PR CI never runs migrations against a real Postgres, so a broken migration is first executed against production.
  - **e2e never runs in CI** (`pr.yml` has no e2e step) — it requires a hand-captured `storageState` (`e2e/README.md:24-37`), so the four authenticated specs are developer-only.
  - `Dockerfile.worker` installs from `pyproject.toml` with no lockfile (`Dockerfile.worker:12` — "no uv.lock yet"), despite `uv.lock` existing at the repo root: the worker image is not reproducible.

### Seed / demo data strategy

- **Status**: 🎭
- **What exists today**: 15 seed scripts under `apps/api/scripts/` (`seed_demo.py`, `seed_clients.py`, `seed_documents.py`, `seed_advice.py`, `seed_onboarding.py`, `demo_orchestration.py`, `bootstrap_dev_tenant.py`, …) plus two large front-end fixture modules — `apps/client/src/lib/demo.ts` (763 lines) and `apps/platform/src/lib/demo.ts` (1056 lines). Some of `demo.ts` is legitimately shared formatting (`formatDate`, `formatSize`, `STATUS_META`, `TONE_CLASSES`) which most importers use. But fixture _data_ leaks into real, signed-in UX in five places.
- **Expected**: _As a user, everything I see is my data or an honest empty state._
  - Demo fixtures confined to a flagged demo mode or deleted.
  - Formatting helpers live in a `format` module, not `demo`.
  - Every unimplemented surface shows an empty state, not fabricated content.
  - Forms that cannot persist are disabled, not silently accepting input.
- **Gaps**:
  - **`apps/client/src/routes/account/+page.svelte:16,78,91-106`** renders a fabricated phone number and postal address from `demo.profile` in the signed-in client's own account page — and the save action validates then discards (`account/+page.server.ts:5-7` "Demo only"). A client edits their address, is told nothing persisted, and sees someone else's fake address on reload.
  - **`apps/client/src/routes/adviser/+page.server.ts:2,7`** returns `demo.adviser` — every client sees the same fictional adviser regardless of their real `crm.client_advisers` assignment. The "request a call" action returns `{requested: true}` and does nothing (`:11-14`).
  - **`apps/platform/src/routes/documents/[id]/+page.svelte:29,46`** derives version history from `versionsByDoc[id]` — real document uids never match demo keys, so the version panel on every real document is permanently empty.
  - **`apps/client/src/routes/transfer/[kind]/+page.svelte:17`** drives the transfer wizard off `demo.wrappers`.
  - All three search bars (see Search).
  - The naming makes this worse: importing `formatDate` from `$lib/demo` trains everyone to treat that module as legitimate, so real-vs-fake is invisible at the import site.

### API surface hygiene

- **Status**: ⚠️
- **What exists today**: 154 routes across 17 routers, consistent naming via `custom_generate_unique_id` (`main.py:32-36`), a generated TS client with CI drift-failure. I cross-referenced every `${apiUrl()}` template in all three portals against the router paths: no UI calls a non-existent endpoint (the portals' own `+server.ts` proxies at `documents/[id]/status`, `documents/[id]/shares`, `templates/[id]/edit/preview` etc. all map to real API routes and re-gate on `can` before proxying — good discipline).
- **Expected**: _As a maintainer, every endpoint has a consumer and every UI call has an endpoint._
  - No orphan endpoints.
  - No UI fetch to a missing path.
  - Client-side proxies re-check permissions.
  - Orphans are deleted, not left as attack surface.
- **Gaps** — endpoints with **no UI consumer**:
  - `GET /me`, `GET /me/tenant`, `GET /me/memberships` (`me.py:11,17,35`) — none is fetched by any portal, yet `CLAUDE.md` and `config.py:126` both tell operators to "find your id on the admin home's `/me` output". That output does not exist; the break-glass bootstrap instruction is unfollowable.
  - `GET /catalog/models`, `GET /catalog/fee-packages` (`catalog.py:52,82`).
  - `POST /admin/tenants/{id}/roles/sync` (`admin.py:262`).
  - `POST /applications/{uid}/start` (`applications.py:299`), `GET /flows/{uid}/versions/{vid}` (`flows.py:247`).
  - `POST /documents/requests/{uid}/cancel`, `PATCH /documents/requests/{uid}`, `POST /documents/requests/{r}/items/{i}/reassign`, `GET /documents/{uid}/requests` (`documents.py:870,901,1359,1639`) — the document-request lifecycle is only partly reachable: staff can create requests but not cancel, amend or reassign them from any UI.
  - `GET /documents/{uid}/url`, `GET /files/{uid}/url` (presign) — superseded by the `/content` proxy, never called.
  - Orphan _route_: `apps/admin/src/routes/users/` + `users/[id]/` exist and are linked only from a dashboard tile (`apps/admin/src/routes/+page.svelte:103`), absent from the sidebar (`+layout.svelte:53-79`) — a whole cross-tenant user directory reachable only by knowing the URL.

---

## Domain summary

| Concern                       | Status | One-line verdict                                                            |
| ----------------------------- | ------ | --------------------------------------------------------------------------- |
| Login (password chain)        | ✅     | Solid, well-classified, unthrottled                                         |
| MFA enrol/verify (TOTP)       | ✅     | Correct; no recovery codes, no admin reset                                  |
| Email verification            | 💥     | `/verify` unreachable in all three portals; template dead                   |
| Set-password (invite)         | ⚠️     | Works; live token echoed into responses + UI                                |
| Forgot-password               | ⚠️     | **Fully built** (CLAUDE.md is wrong); unthrottled, wrong sender             |
| Logout                        | ✅     | Revokes remotely, degrades to local-only silently                           |
| Session refresh/expiry        | ✅     | Best-engineered area; no return-to on expiry                                |
| Security settings page        | 🎭     | **Change password is fake on platform + admin**                             |
| Permission registry vs UI     | ⚠️     | Excellent registry; blank model tenant ⇒ nothing grantable                  |
| Claims contract & privilege   | ✅     | Clean; `SYSTEM_USER_IDS` split-brain vs SQL `is_system()`                   |
| RLS coverage + FORCE          | ⚠️     | All 48 tables covered; `auth.tenants USING (true)` leaks the firm directory |
| Book-scoping consistency      | ⚠️     | Documents/tasks/CRM scoped; cases, KYC, shares are not                      |
| Portal trust boundary         | ✅     | Genuinely hard, SQL-enforced, double-belted                                 |
| Isolation test coverage       | ⚠️     | Seven strong suites; flows/tasks/KYC/shares untested; skips silently        |
| Email & notifications         | ⚠️     | Four producers live; `notify.*` has none; `sender_for` a stub               |
| Search (×3 portals)           | 🎭     | All fabricated; no search endpoint exists at all                            |
| Background machinery          | ⚠️     | Real queue discipline; no healthcheck, no metrics, no DLQ visibility        |
| Healthchecks & error tracking | ❌     | Static `/health`; zero error tracking anywhere                              |
| Rate limiting                 | ❌     | None; the 0024 public throttle is wired to nothing                          |
| CORS / uploads / secrets      | ⚠️     | Caps enforced; unvalidated origin regex; keys in response bodies            |
| Deploy pipeline               | ⚠️     | Strong PR CI; migrate-before-build, fire-and-forget release, no rollback    |
| Seed / demo data              | 🎭     | Fixture data in five real signed-in surfaces                                |
| API surface hygiene           | ⚠️     | No broken calls; ~14 orphan endpoints incl. all of `/me`                    |

## Top-10 cross-cutting gaps, in priority order

1. **Change password is a lie on two of three portals.** `apps/platform/src/routes/account/security/+page.server.ts:106` and `apps/admin/…:106` validate input, return `{passwordChanged: true}`, and never call WorkOS. Staff and Graphene ops believe they have rotated a credential that is unchanged. The working implementation exists at `apps/client/…/security/+page.server.ts:106-144`.
2. **No rate limiting on any unauthenticated surface**, and the hardening that was supposed to provide it is dead: `flows.public_start_throttle` + `public_onboarding_max_per_hour` are read by nothing and their endpoint doesn't exist. Login (password spraying), forgot-password (email bombing via our Resend account) and `/share/{token}` (enumeration) are all unbounded and unmeasured.
3. **`auth.tenants` reads with `USING (true)`** (`0001_auth_core.sql:577`). Any authenticated principal — including a client end-user — enumerates every firm's name, alias, website, WorkOS project id and full branding `theme`. This is a cross-tenant leak in a product whose central promise is tenant isolation, and it bypasses the otherwise-excellent client-portal boundary.
4. **A fresh install cannot grant a single permission.** The model tenant is seeded blank (`0001_auth_core.sql:631`), no migration or delta creates any role, so `clone_model_roles` clones nothing and every `require_permission` endpoint 403s. The documented escape hatch (`SYSTEM_USER_IDS`, "find your id on the admin home's `/me` output") is unfollowable because no portal fetches `/me`, and SQL `auth.is_system()` doesn't honour the allowlist anyway (`0001:460` vs `app/auth.py:172`) — so break-glass passes application gates then sees nothing under RLS.
5. **Zero error tracking and zero metrics across the whole platform.** No Sentry, no OTel, no Prometheus, text-only logs. Combined with `/health` being a static string and the `render` worker having **no healthcheck**, a wedged worker, a stalled outbox, a Resend outage or a failing forgot-password (deliberately swallowed at `internal.py:103`) are all invisible until a user complains.
6. **All three search bars are fabricated, and no search endpoint exists.** An adviser searching their own client list is shown four fictional names (`apps/platform/src/lib/SearchBar.svelte:7-12`) then dumped on `/clients`; an ops user sees five fictional firms. To a user in a regulated multi-tenant product this is indistinguishable from a cross-tenant data leak.
7. **Book-scoping is applied inconsistently to the most sensitive tables.** `crm.kyc_checks` (identity-verification outcomes, `0026:43`), `flows.cases`/`case_steps` (`0021:151-171`) and `documents.shares` (with full write, `0017:37`) are tenant-wide, while the _documents_ attached to those same cases are correctly book-scoped (`0021_documents_scope.sql`). A book-scoped adviser reads every client's KYC and revokes share links for documents they cannot see.
8. **Email verification is structurally broken.** `/verify` in all three portals gates on a `PENDING_COOKIE` that nothing sets, so the route always redirects to `/login`; `email.verify_email()` is unreferenced; and login now conflates verification with a password _reset_ (`internal.py:87-89`), resetting a working credential. `docs/auth.md:41` documents the dead flow as live.
9. **Deploy migrates before it builds, then fires and forgets.** `deploy.yml:24-35` applies migrations, _then_ builds, _then_ POSTs a webhook whose result is never checked — so a build failure leaves the DB ahead of the running code, and a failed Coolify release is a green check. No rollback path, no post-deploy smoke test, and migrations are never exercised against a real Postgres in PR CI.
10. **Demo fixtures are embedded in real signed-in UX.** A client's own account page shows a fabricated phone and postal address and silently discards edits (`apps/client/src/routes/account/+page.svelte:78-106` + `+page.server.ts:5`); every client is shown the same fictional adviser (`adviser/+page.server.ts:2`); document version history is permanently empty (`documents/[id]/+page.svelte:46`). Importing `formatDate` from `$lib/demo` normalises the module, making real-vs-fake invisible at the call site.

Two secondary items worth queuing behind these: **`FLOWS_ORCHESTRATOR_V2` cannot be enabled on the deployed stack** (absent from `docker-compose.yaml`, yet `journeys/+page.svelte:122` instructs users to set it, and it gates `cases:*`, `/applications/*` and the only `notify.*` producer); and **the DB-backed test suite skips rather than fails without Docker** (`tests/conftest.py:44,55`), so the entire RLS isolation proof can silently not run behind a green gate.
