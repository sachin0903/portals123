# AGENTS.md

Orientation for working in this repo — for humans and for coding agents. Keep it a **map**: what
this is, how to run it, the rules that apply to every change, and where the real documentation
lives. Depth belongs in `docs/`.

This file used to be `CLAUDE.md` and had grown to 229KB, all of it loaded into every session. That
made the always-present context mostly a second copy of documentation that already existed. The
detail was not deleted — it moved to [`docs/platform-state.md`](docs/platform-state.md) and
[`docs/gotchas.md`](docs/gotchas.md), both linked below.

## What this is

A multi-tenant financial services platform. Adviser firms (and their direct/exec-only clients) are the tenants. Identity is WorkOS; authorisation is owned by this app. Postgres Row Level Security enforces tenant isolation at the database. Greenfield, early stage: auth is working and the **auth/RLS/audit foundation** (`0001_auth_core`) is in place — the request-layer claims contract is wired and tenant isolation is proven by a real-Postgres test. The **domain schema** (`0003_platform_crm` onward; `0027_tenant_storage` — the per-tenant object-storage registry — is in) is being built forward per the data-architecture decision records.

## Monorepo layout (moon + proto, pnpm + uv)

```
apps/client   SvelteKit — client portal: end users (public)
apps/platform SvelteKit — platform portal: tenant staff — advisers, compliance, paraplanners (public, role-based)
apps/admin    SvelteKit — Graphene internal admin: ops, RMs (to be network-locked)
              Per-tenant sidebar + in-firm search mirror the platform IA (`@graphene/ui/nav`); do not invent a second structure.
apps/api      FastAPI   — business logic, authz, audit, DB (+ app/lake: in-process per-tenant DuckLake reads; external: Litestar partner API)
pkgs/api-client  generated TS client from the API's OpenAPI (Hey API)
pkgs/auth        framework-agnostic WorkOS auth helpers (error classifiers, cookies, passwordAuth, form validation)
pkgs/ui          shared shadcn-svelte components + the design theme (Tailwind 4)
pkgs/config      shared eslint / prettier / tsconfig
pkgs/morgan-lloyd  Dynamics 365 CRM client (Python)
pkgs/seccl         generated Seccl payload models from the vendored Postman collection (Python)
pkgs/sei           SEI Wealth Platform (SWP) API client — mTLS + OAuth, cash receipts (Python)
pkgs/book-sink     the provider pipelines' write side: a tenant's slice → inbound → the book (Python)
docs/                architecture + workflow docs
infrastructure/      Coolify/deploy notes
```

All three portals share the auth flow via `pkgs/auth`; each keeps a thin `src/lib/server/workos.ts` that reads SvelteKit env and wires the shared helpers. **There is no self-service signup** — accounts are provisioned in WorkOS — so the portals expose login, MFA, email-verify, logout and security settings, but no signup route. (`signupErrorMessage`/`validateSignup` remain in `pkgs/auth` for a future invite flow.)

## Run

```
proto use                      # installs pinned Node 24, pnpm 11, uv, moon v2 (Python is uv-owned)
pnpm install                   # JS deps + git hooks (moon sync vcs-hooks)
uv sync --project apps/api     # Python deps
moon run :dev                  # all dev servers (client :5173, admin :5174, platform :5175, api :8000, external :8010)
                               # — brings up local Postgres 18 + RustFS and migrates to head first
moon run :lint :check :typecheck :test   # what the gates run
```

The local infra (`docker-compose.local.yaml`, a non-default name so plain `docker compose` still resolves to the prod `docker-compose.yaml`) is **local dev only** — Postgres 18 plus RustFS (S3-compatible object storage for branding assets; S3 API on `:9000`, console on `:9001`, creds `rustfsadmin`). Production uses an external managed Postgres and an external object store. First init runs `migrations/bootstrap/01-roles.sql` (so the DB roles exist before `api:migrate`) and a one-shot `rustfs-init` creates the public-read `branding` bucket plus the private `platforms` bucket (per-tenant `t-{uid}` buckets are minted by the API at tenant creation — `docs/storage.md`). `api:db-up` (and `pnpm storage:up` / `worker:up`) create the external `graphene-dev` network first — compose will not, because it is `external` so `down` cannot detach a standalone Postgres. If the default ports (`5432`, `9000`/`9001`) are taken on your machine, put overrides in the **root `.env`** (gitignored, templated by the tracked root `.env.example`; docker compose reads it automatically since the `api:db-*` tasks run from the workspace root): `DB_PORT=…`, `RUSTFS_PORT=…`, `RUSTFS_CONSOLE_PORT=…`. WorkOS / Seccl / `INTERNAL_API_TOKEN` stay in `apps/*/.env.local` and `services/*/.env`; do not copy them into the root `.env` so `db-up` can parse. Local compose interpolations default those blank. That file is also where the **shared warehouse + object store** lives — `DATALAKE_WRITER_DSN`, `S3_CORE_BUCKET`, `S3_ENDPOINT_HOST`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and `BOOK_WRITER_DSN` (the provider pipelines' own login on the portals DB — libpq keyword form, because interlace interpolates it into `postgres:${BOOK_WRITER_DSN}` for DuckDB's postgres extension) — because the three provider sync services read `/.env` before their own (`envFile: ['/.env', '.env']`); each used to carry its own prefixed copy of all five, so one value lived in three files and a wrong endpoint reads as "the catalogue is empty" rather than as a misconfiguration — and point the URLs in `apps/api/.env.local` (`DATABASE_URL`/`MIGRATION_DATABASE_URL`/`DATALAKE_*`/`S3_*`) at the same ports. `pnpm db:reset` wipes and re-initialises. After a reset (or whenever login says you have no access to the portal), `pnpm db:bootstrap` (`moon run api:bootstrap-dev`) recreates the Graphene tenant + the owner logins (`mark@grapheneplatforms.com` and `gio@grapheneplatforms.com`, platform and operations, `status='active'`) and the local WorkOS host routing for `:5173`/`:5175`/`:5174`. The `pnpm db:*` scripts are thin aliases for `moon run api:db-{up,down,reset}` plus `api:bootstrap-dev` (the moon tasks own the compose invocation; `runFromWorkspaceRoot` so the root-relative `-f`/bind-mount paths resolve, `runInCI: false` so they never fire in CI). **Migrations are automatic locally**: `moon run api:dev` — and `moon run :dev` — depend on `api:db-migrate`, which waits for the stack to be healthy (`up -d --wait`) then runs `alembic upgrade head`. The plain `api:migrate` task stays standalone because the deploy pipeline runs it against managed Postgres (see `.github/workflows/deploy.yml`); bring the DB up yourself before a standalone `moon run api:migrate`.

Per-project: `moon run client:dev`, `moon run platform:dev`, `moon run api:dev`, `moon run api:external-dev` (firm API, `python -m external`, `:8010`, `EXTERNAL_STUB` defaults true), `moon run api:migrate`, `moon run api:db-up` (`api:db-down` / `api:db-reset`), `moon run api:bootstrap-dev` (login identity + WorkOS hosts), `moon run api-client:generate` (dumps the schema itself via `api:dump-openapi` — the API does NOT need to be running, and generating from a live URL bakes that origin into the committed client).

## Conventions

- Conventional Commits, enforced by a commit-msg hook. `main` is meant to be protected (PR + green CI); configure that in GitHub.
- Pre-commit: prettier + ruff (format/lint staged files). Pre-push: `moon run client:lint platform:lint admin:lint auth:lint`, `:check`, `:typecheck`, `:test`. Pre-push runs **serially** on purpose (see gotchas).
- Python is source of truth for API types; the TS client is generated via Hey API and a CI step fails on drift. Don't hand-edit `pkgs/api-client/src`.
- DB: app role is DML-only and subject to RLS; a separate elevated role runs migrations. Every tenant-scoped table must ship its RLS policy in the same migration. Migrations are expand/contract (backwards-compatible), use `CREATE INDEX CONCURRENTLY` + a sane `lock_timeout`.
- **Ids:** `id` is internal (int/bigint, never in a URL); `uid` is public and is a bare
  12-character nanoid from `app/ids.py::new_id` — no type prefix. The tenant uid is the one
  held-back kind (still `ten_<hex>`: it becomes an S3 bucket name and a SQL schema identifier).
  `docs/data-architecture.md` Decision 9; `tests/test_public_ids.py` is the tripwire.
- British English in prose/docs.

## Standards (every change — humans and agents)

- **Tests ship with the code.** Non-trivial logic gets unit tests in the same change; a bug fix adds a test that fails before it. Co-locate `*.test.ts` (vitest) / `test_*.py` (pytest); run `moon run <project>:test`.
- **Gates stay green.** `moon run :lint :check :typecheck :test` passes before "done". Don't weaken a gate to pass it (skips, `--no-verify`, disabled rules, loosened types) without the user's explicit OK.
- **Verify external shapes; don't assume them.** Confirm SDK/API field names against installed types or a real response, then pin them in a test (see the MFA flow in `docs/auth.md`).
- **Secrets never hit git or logs.** Only `.env*.example` is tracked; never log tokens, sessions, or pending-auth tokens.
- **Match surrounding code**, and **update the docs a change invalidates** in the same change:
  the subject doc in `docs/`, or [`docs/platform-state.md`](docs/platform-state.md) when it spans
  several, or [`docs/gotchas.md`](docs/gotchas.md) when it is a trap. **Not this file** — AGENTS.md
  is a map, and it grew to 229KB the last time that rule pointed here.

## Before you debug the tooling

[`docs/gotchas.md`](docs/gotchas.md) — the platform traps that cost real time to find, each one
recorded because the symptom did not name the cause: moon v2's renamed config keys, the gate task
names that differ by project, pnpm's build allowlist, the vitest project split in `pkgs/ui`, the
bits-ui `Checkbox` double-toggle, the shared field-shell classes, the API's `.env` resolution.
Read it before concluding a tool is broken.

## Where the documentation is

[`docs/platform-state.md`](docs/platform-state.md) is the cross-cutting narrative — what is built,
why it is shaped that way, and what was deliberately left undone. Read it for background; you do
not need it to make a small change. The subject docs below are authoritative on their own subject.

| Doc                                                                    | For                                                                                   |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| [`docs/project-context.md`](docs/project-context.md)                   | The full background and decision log                                                  |
| [`docs/stack-and-workflows.md`](docs/stack-and-workflows.md)           | The stack rationale                                                                   |
| [`docs/auth.md`](docs/auth.md)                                         | The auth flow                                                                         |
| [`docs/api.md`](docs/api.md)                                           | The FastAPI backend — endpoints, config, DB roles, RLS                                |
| [`docs/packages.md`](docs/packages.md)                                 | The shared packages — generated client, auth helpers, configs, ui                     |
| [`docs/delivery.md`](docs/delivery.md)                                 | How we run Jira/Confluence — risk-class gating, ticket conventions, the DoD           |
| [`docs/flows.md`](docs/flows.md)                                       | The config-driven flow/process engine; onboarding is its first flow                   |
| [`docs/onboarding-legacy.md`](docs/onboarding-legacy.md)               | **What the legacy app actually did** — read before changing onboarding semantics      |
| [`docs/client-record.md`](docs/client-record.md)                       | The client record's five editable sections, and beneficiary nominations               |
| [`docs/client-vocabularies.md`](docs/client-vocabularies.md)           | Closed client lists and each provider's codes — title, employment, wealth, funds      |
| [`docs/client-classifications.md`](docs/client-classifications.md)     | Client books — which portal design, and whether they may move money                   |
| [`docs/client-groups.md`](docs/client-groups.md)                       | Client groups — tags such as a business stream, and the design they select            |
| [`docs/tasks.md`](docs/tasks.md)                                       | Standalone tasks — assignable to staff, roles or clients                              |
| [`docs/reviews.md`](docs/reviews.md)                                   | Four-eye checks — hold a designated action until a second person approves             |
| [`docs/actions.md`](docs/actions.md)                                   | Domain actions — firm policies, runs, provider steps                                  |
| [`docs/design-language.md`](docs/design-language.md)                   | **The UI law** — always @graphene/ui, and how a VALUE is printed                      |
| [`docs/portal-customisation.md`](docs/portal-customisation.md)         | Client-portal composition — pages, blocks, variants, looks, the studio                |
| [`docs/content.md`](docs/content.md)                                   | The firm-authored news/article/guide library                                          |
| [`docs/sei.md`](docs/sei.md)                                           | The SEI file-drop custodian — SFTP ingest, PGP, models, tenant fan-out                |
| [`docs/sei-data-sources.md`](docs/sei-data-sources.md)                 | The SEI source map — all 47 reports, fill rates, column-level mappings                |
| [`docs/contributions.md`](docs/contributions.md)                       | The money ledger — contributions, transfers, expectations, provider calls             |
| [`docs/gia-isa-illustrations.md`](docs/gia-isa-illustrations.md)       | GIA and ISA illustrations — the monthly engine, the snapshot, the firm flag           |
| [`docs/mi-reports.md`](docs/mi-reports.md)                             | Management reports — shipped queries, the global switch, CSV and Excel                |
| [`docs/aml.md`](docs/aml.md)                                           | AML/identity screening on ComplyCube and the tipping-off control                      |
| [`docs/integrations.md`](docs/integrations.md)                         | SEI platform approvals, feature toggles, job runs, the admin Clients and Accounts tab |
| [`docs/partner-api.md`](docs/partner-api.md)                           | Firm-facing partner API — versioning, clients, checks, keys                           |
| [`docs/messaging.md`](docs/messaging.md)                               | Client↔adviser chat — schema, RLS, live push, attachments                             |
| [`docs/vendor-integration-tests.md`](docs/vendor-integration-tests.md) | Live tests against the custodians' own test environments                              |
| [`docs/audit/README.md`](docs/audit/README.md)                         | The July 2026 functional audit — 210 functions traced end to end                      |
| [`docs/audit/VERIFIED-STATUS.md`](docs/audit/VERIFIED-STATUS.md)       | That audit's re-verified status — read before assuming a feature works                |
| [`docs/platform-model.md`](docs/platform-model.md)                     | The canonical platform model — conformed shape, OLTP vs lake, the sync                |
| [`docs/provider-pipeline.md`](docs/provider-pipeline.md)               | How a custodian's data reaches a firm's book                                          |
| [`docs/gotchas.md`](docs/gotchas.md)                                   | Hard-won platform traps — read before debugging tooling                               |
| [`docs/platform-state.md`](docs/platform-state.md)                     | What is built and why — the cross-cutting narrative and migration history             |
| [`infrastructure/README.md`](infrastructure/README.md)                 | The deploy pipeline                                                                   |

Anything not listed: `ls docs/` — there are more, and they are named for their subject.

## Keeping this file small

The rule that keeps it useful: **AGENTS.md answers "where do I look?", never "what happened?"**. A
change that needs explaining goes in the subject doc, or in `docs/platform-state.md` if it spans
several. If you find yourself adding a paragraph here about a specific feature, it belongs in
`docs/`, and this file gets at most a line pointing at it.
