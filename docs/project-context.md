# Project Context

The full background for the Graphene platform: vision, architecture decisions, what's been resolved vs left open, hard-won gotchas, and prioritised next steps. This is the brain dump for anyone (or any agent) picking the project up. Pairs with `stack-and-workflows.md` (stack rationale + pinned versions) and `auth.md` (auth flow).

---

## 1. Product vision

A modern multi-tenant financial services platform serving adviser firms and their clients, including direct/execution-only clients. Goals: meet SOC 2 / ISO 27001, support enterprise customers with SSO, support self-service users, run on a single shared application platform with strict tenant isolation, scale from startup to enterprise, and minimise operational overhead.

Tenancy shape (settled): there is always an adviser **firm** (the tenant), and clients hang off a firm. "Direct-to-consumer" clients are not a separate construct: they are execution-only/self-managed clients that an adviser firm has signed on, rather than advised clients. There is **no separate "house" org** for D2C. `execution_only` vs `advised` is an attribute on the client, and it's a real regulatory/permissions distinction (no advice/suitability duty for exec-only; it gates certain actions) even though the portal mechanics are the same.

## 2. Architecture at a glance

- **Frontend:** SvelteKit 2 + Svelte 5 (runes) + Tailwind 4 + shadcn-svelte. Three deployments: `apps/client` (client portal, public), `apps/platform` (platform portal for tenant staff — advisers, compliance, paraplanners — public) and `apps/admin` (Graphene staff tenant admin, internal).
- **Backend:** FastAPI (async) + Pydantic v2 + SQLAlchemy 2 + Alembic. One API serves all portals.
- **Identity:** WorkOS (AuthKit, custom UI). WorkOS answers "who is this user"; the app answers "what can they do".
- **Data:** PostgreSQL 18, single shared cluster/schema, tenant isolation via `organisation_id` + Row Level Security.
- **Monorepo:** moon v2 + proto, pnpm workspaces (JS) + uv workspace (Python). Types flow Python → OpenAPI → generated TS client (Hey API).
- **Infra (direction, treated as out-of-scope in these sessions):** self-hosted Proxmox + Coolify, self-hosted GitHub runner for deploy/migrations, GitHub Actions CI.

## 3. Multi-tenancy & RLS (the core security design)

This is the most important and highest-stakes part of the system.

- Every tenant-scoped row carries `organisation_id`. RLS policies filter on it. This is the firm-vs-firm boundary.
- **RLS through a connection pooler is the thing that silently breaks isolation.** `SET app.current_org_id` is session-scoped; under PgBouncer transaction pooling one session is shared across tenants, so it leaks. The rule: every request runs in a transaction and uses `SET LOCAL` / `set_config(..., is_local => true)` so the setting is scoped to that transaction only. This is already stubbed in `apps/api/app/db.py` (`tenant_session`).
- The app connects as a **non-superuser, non-owner role**, tables use `FORCE ROW LEVEL SECURITY` (owners bypass RLS otherwise). A **separate elevated role** runs Alembic migrations (DDL). Two distinct DB URLs: `DATABASE_URL` (app, DML, RLS-bound) and `MIGRATION_DATABASE_URL` (migrations, elevated).
- **Intra-org row scoping is required, not just org-vs-org.** Pure RBAC answers "can an adviser edit clients at all", not "which clients". An adviser edits their own book; a senior adviser / ops sees the whole firm; an exec-only/self-managed client only ever touches their own record. That's relationship/attribute-based (ReBAC/ABAC), not a role. Bake an owner/assignment concept into the client model from day one.
- Sketch agreed for the first real table:

```
clients
  id                uuid pk
  organisation_id   uuid   -- RLS partition key
  client_user_id    uuid   -- the WorkOS user who *is* this client (nullable until invited)
  assigned_advisor_id uuid -- nullable; use a client_advisors join table instead if more than one adviser
  service_type      text   -- 'advised' | 'execution_only'
```

- RLS predicate shape (two layers: org first, then relationship). The app sets `app.current_org_id`, `app.current_user_id`, and a `app.see_all_in_org` boolean (computed by the authz layer from role) per request:

```sql
USING (
  organisation_id = current_setting('app.current_org_id')::uuid
  AND (
    current_setting('app.see_all_in_org')::bool
    OR assigned_advisor_id = current_setting('app.current_user_id')::uuid
    OR client_user_id      = current_setting('app.current_user_id')::uuid
  )
)
```

- The app authz layer separately enforces the action (`client.edit`, exec-only self-execute) on top of what RLS made visible — belt and braces, so a bug in one isn't a breach.
- Platform admins crossing tenant boundaries need an explicit, heavily-audited break-glass path, not RLS bypass sprinkled around.

## 4. Authorisation model

RBAC foundation (Adviser, Client, Admin, Compliance Officer, etc.) plus tenant-defined custom roles, plus the relationship scoping above. Permissions are fine-grained (`client.view`, `client.edit`, `portfolio.edit`, `report.generate`, `user.invite`, `organisation.settings.manage`, …). The `see_all_in_org` boolean keeps RLS policies readable instead of branching on roles inside SQL.

## 5. Audit logging

Immutable from the tenant's point of view; trust model is platform-as-custodian (we are trusted on data integrity). That said, SOC 2 / enterprise questionnaires will still ask how _our own staff_ can't quietly edit logs, so: app role gets INSERT-only on the audit table (UPDATE/DELETE revoked), restrict who can read/touch it, log access to the logs, and ideally ship to an external WORM store (S3 Object Lock) for true immutability. Capture actor, on-behalf-of (platform admin acting in a tenant), request id, resource, before/after, timestamp. Note before/after of financial data carries its own PII/retention implications, and "right to erasure" vs immutable logs vs backups needs a story.

## 6. Documents / object storage

Bucket-per-tenant (S3-compatible), cleaner than shared prefixes. Provisioning a firm includes minting a bucket. Backend mints per-request signed URLs / scoped credentials; never hand a tenant a static key. **Document download is a classic cross-tenant leak**: the download endpoint is separate code from RLS, so it must enforce the same org + ownership scope (section 3) as the DB, plus encryption at rest, short-lived signed URLs, virus scan on upload.

## 7. Portal topology

Three portals, three frontend deployments. The client portal (`apps/client`) and the platform portal (`apps/platform`, used by tenant staff — advisers, compliance, paraplanners) are both external and tenant-facing public apps. Admin is internal Graphene-only tenant management with cross-tenant reach, so it's its own app (`apps/admin`) on a separate ingress that can sit behind the corp VPN / IP allowlist (or Cloudflare Access / Tailscale). **Locking the admin frontend is only half the control** — the admin API surface must be network-restricted too, otherwise tenant-management endpoints stay reachable to anyone with a platform-admin token. One FastAPI backend serves all three; admin endpoints get namespaced + network-restricted + the break-glass audit.

All three portals are now auth-gated and share `@graphene/auth` (admin was previously a bare skeleton with no auth; it now wires the same package and the same thin per-app glue). There is **no self-service signup** in any portal — accounts are provisioned in WorkOS.

## 8. Resolved decisions

- Stack: fully-modern pinned versions (see `stack-and-workflows.md`). moon v2. mypy (not pyright). No worker/scheduler yet. ESLint+Prettier over Biome (Biome's Svelte support too weak in 2026). Node 24 LTS over 26.
- Data model now being built forward (decision records: `data-architecture.md`, `identity-and-access.md`; north-star reference: `proposed-schema.sql`). `0001_auth_core` (auth/RLS/audit foundation) and `0027_tenant_storage` (per-tenant object-storage registry, `storage.md`) are in; `0003_platform_crm` next.
- RLS context uses the WorkOS + **pre-resolved-claims** idiom (`request.jwt.claims` + `auth.resolve_claims`), superseding the `app.current_org_id` GUC sketch in §3 below — see `identity-and-access.md` §5–6 and `api.md`.
- Whether a client has more than one adviser (the §9 open question) is resolved: staff↔client assignment is a join table (`crm.client_advisers`), not a single `assigned_advisor_id`. Since `0245` (Oct 2026) that table is the client's team: **one adviser per client**, any number of paraplanners, administrators and support staff.
- Tenancy: firm org always present; D2C = exec-only flag on client, no house org.
- **Identity isolation: project-per-tenant (revised July 2026).** White-label — each firm is sold its _own_ platform, so clients/staff of different firms **never share a login**. The tenant boundary is a **WorkOS project per tenant** (staging + production environments within it; portal = WorkOS org: Client Portal / Platform). Tenant = project, stage = environment, `org_id` claim = portal. The single Postgres stays shared + RLS with `tenant_id` sourced from the environment. Authoritative record + consequences: `identity-and-access.md` **§0**. Provisioning is a manual runbook (no project-creation API); tens of high-touch firms make that acceptable.
- Auth: WorkOS custom UI (not hosted AuthKit). SvelteKit owns the sealed session; FastAPI verifies the access-token JWT via JWKS and sets the RLS context. MFA is opt-in TOTP. All three portals (client, platform, admin) are auth-gated and share `@graphene/auth`; there is no self-service signup — accounts are provisioned in WorkOS.
- Logout uses full WorkOS `getLogoutUrl()` (ends the IdP session, needs an App homepage URL configured) rather than local-only cookie clear — the more defensible behaviour for a fintech.

## 9. Open decisions

- **Single vs multiple advisers per client.** If a client can ever have more than one adviser, seed a `client_advisors` join table instead of `assigned_advisor_id`. Decide before the first migration.
- Whether/when to enforce MFA by role (mandatory for advisers/admins, optional for clients). Needs orgs + roles wired first since enforcement keys off the `role`/`org_id` claims, which are currently null.
- Monitoring stack specifics (recommended: Prometheus + Grafana + Loki + Sentry, with PII scrubbing on Sentry). Tension noted: "minimise operational overhead" vs self-hosting Postgres + Coolify + runners + a full observability stack.
- Whether enterprise customers will contractually demand isolated data / their own keys / residency (could force a tenant out of the shared DB later — keep `organisation_id` as the partition key everywhere so a tenant can be peeled out).

## 10. Compliance hooks already in place

Secret scanning (gitleaks) + dependency/image scanning (trivy, pnpm/uv audit) in CI; CODEOWNERS + intended branch protection as documented access control; Conventional Commits + protected main as change-management evidence; the two-role DB + FORCE RLS + INSERT-only audit pattern; OpenAPI→TS drift check.

## 11. Gotchas log (why each fix exists)

These were all discovered the hard way during setup; the reasons matter for edge cases.

- **moon v2 renames.** v1→v2: project `type:`→`layer:`; `vcs.manager`→`vcs.client`; task `local: true`→`preset: 'server'`; toolchain file is `.moon/toolchains.yml` (plural). The `typescript` toolchain was removed from `toolchains.yml` because it auto-synced TS project references, generating `pkgs/*/tsconfig.json` files that extended a non-existent root `tsconfig.options.json` and broke vite. Python runs as plain `uv` command-tasks because moon's python toolchain is still `unstable_*`.
- **pnpm 11.5 build allowlist** lives in `pnpm-workspace.yaml` as `allowBuilds: { esbuild: true }`. The old `package.json` `pnpm.onlyBuiltDependencies` is ignored; an empty allowlist makes `pnpm install` exit 1.
- **vite/vitest type clash.** Importing `defineConfig` from `vitest/config` dragged in a second vite version whose plugin types didn't match, breaking `svelte-check`. Fix: `defineConfig` from `vite`, no `test` block in vite.config, and `--passWithNoTests` on the moon test task.
- **Lazy WorkOS client.** `new WorkOS('')` throws `NoApiKeyProvided`; constructing at import time broke `vite build`'s analyse step (no env). Hence `getWorkOS()`.
- **`.env.local`.** Vite reads it automatically; pydantic-settings was pointed at `.env` only, so the API silently missed `WORKOS_CLIENT_ID`. Config now reads `(".env", ".env.local")`.
- **ESLint.** `@typescript-eslint/no-empty-object-type` off (SvelteKit `app.d.ts` uses empty interfaces for declaration merging). Ignore `**/*.timestamp-*.mjs` (vite writes/deletes these, eslint raced and hit ENOENT). Pre-push is `parallel: false` so eslint and the vite-invoking `check` task never overlap. ESLint runs per-app via moon (not installed at root). Reactive props captured in a plain `const` need `$derived`.
- **ruff B008.** `fastapi.Depends`/`fastapi.Security` added to `extend-immutable-calls` — the DI-in-defaults idiom is correct for FastAPI.
- **WorkOS dashboard prerequisites:** enable email+password; set a Sign-out redirect AND an **App homepage URL** (logout 500s without the latter); email verification on → flow routes to `/verify`; MFA must be enabled in the environment for enrol/challenge to work.

## 12. Next steps (roughly prioritised)

1. **Commit the auth + MFA work** (it was complete but uncommitted at handoff) and configure branch protection on `main` in GitHub.
2. ~~Settle the data model and write the first RLS-backed migration.~~ **Done** for the foundation: `0001_auth_core` (auth/RLS/audit) is in, one adviser per client resolved via `crm.client_advisers`. Next is `0003_platform_crm` (the domain tables + client-scoped RLS).
3. ~~Finish wiring the RLS context in `tenant_session`.~~ **Done**: `tenant_session` sets `SET LOCAL ROLE authenticated` + `request.jwt.claims` via `auth.resolve_claims`, and `tests/test_rls.py` proves cross-tenant reads are blocked (real Postgres via testcontainers). Remaining: **apply `tenant_session` to the tenant-scoped routes** as endpoints land.
4. **Land the project-per-tenant model** (`identity-and-access.md` §0). **Done (expand phase):** `0001` reshaped — `auth.users.tenant_id` + `UNIQUE(tenant_id, email)` (same email is a distinct login per tenant), `auth.tenants.workos_project_id`, the `auth.tenant_environments` registry, and `auth.resolve_claims` now derives the tenant from the login (org = portal). **Staged next (contract phase), in order:**
   - a) **Rewrite provisioning/sync/admin** from org-per-tenant to project-per-tenant: an operator runbook creates the WorkOS **project** (+ prod/staging envs) manually, then an admin action registers it in `auth.tenant_environments` and seeds the two portal orgs (Client Portal / Platform) + role templates via that env's key. Retires the old "create org via API" flow. Move portal orgs onto the memberships (`UNIQUE(user_id, workos_organisation_id)`) and drop the transitional `auth.tenants.workos_organisation_id`.
   - b) **Multi-environment token verification** — **done**: the API is shared, so `auth.py` verifies each token against the environment that issued it (issuer → `tenant_registry` cache of `auth.tenant_environments` → that env's JWKS), with a single-env fallback for dev.
   - b2) **Secret authority (API-side)** — **done**: `auth.environment_secrets` (Fernet-encrypted api key + cookie password, service_role-only), `app/crypto.py` (`SECRETS_MASTER_KEY`), the admin **register-environment** / **set-secrets** / **refresh** endpoints, and the internal `GET /internal/env-config?host=…` the shared portal will call (guarded by `INTERNAL_API_TOKEN`). TS wiring **partly done**: `pkgs/auth` `resolveEnvironment(host)` (host→env via `/internal/env-config`, cached, unit-tested) and the API's `ALLOWED_ORIGIN_REGEX` CORS. **Remaining (atomic + live-verified):** migrate all three portals' `workos.ts`/`hooks.server.ts` + the login/logout/mfa/verify/security routes from the module-level single-env `getWorkOS()`/`COOKIE_PASSWORD`/`CLIENT_ID` to per-request host resolution (this MUST be atomic per app — sealing a session with one env's cookie password and unsealing with another's breaks auth) with a dev single-env fallback; the admin UI to register + key an environment; branding-by-host in the layout. Then the provisioning/sync org→portal rewrite and backfill `auth.users.tenant_id` → **NOT NULL**. These need a running API + a real WorkOS project to verify.
   - c) App authz layer (permission checks) + optional role-based MFA enforcement.
5. **`0003_platform_crm`** — the domain tables (crm/platform/compliance/integrations) + client-scoped two-layer RLS + `resolve_claims` filling `contact_ids`/`account_ids`.
6. Object storage (bucket-per-tenant) — **foundation done** (`storage.md`): `0027_tenant_storage` registry, automatic bucket + best-effort service-user provisioning at tenant creation, branding on the tenant bucket's public prefix. Remaining: document upload/download with the ownership scope and signed URLs (the `documents.*` schema) and the core-bucket ingestion/fan-out. The datalake itself is in (`datalake.md`): per-tenant DuckLake catalogues in the `datalakes` DB, read in-process via `app/lake` (Arrow/DataFrames, lake↔OLTP joins); the ETL writer is pending interlace's Postgres-catalogue support.
7. Immutable audit logging (INSERT-only table + external WORM).
8. Monitoring/observability with PII scrubbing.
9. Background processing (worker/scheduler) when report generation / notifications / long-running workflows arrive.

## 13. Where things live

- Auth: framework-agnostic logic in `pkgs/auth/src/*` (`@graphene/auth`); per-app glue in `apps/{client,platform,admin}/src/lib/server/{workos,auth,validation}.ts` + `apps/{client,platform,admin}/src/hooks.server.ts`; route UI in `apps/{client,platform,admin}/src/routes/{login,verify,mfa,logout}/` and `.../routes/settings/security/` (no `signup/` route); `apps/api/app/auth.py`, `apps/api/app/routers/me.py`.
- DB / RLS context: `apps/api/app/db.py` (`get_session`, `tenant_session`), `apps/api/migrations/`.
- Config: `apps/api/app/config.py` (pydantic-settings), `.env.local` for local secrets.
- Tooling: `.moon/` (workspace, toolchains, `vcs.hooks`), `.prototools`, `pnpm-workspace.yaml`,
  `scripts/hooks/`, root `package.json`, `apps/api/pyproject.toml`, `pkgs/config/`.
- CI/governance: `.github/workflows/`, `.github/CODEOWNERS`, `.github/pull_request_template.md`.
