# Platform Tech Stack & Engineering Workflows

**Status:** Draft for approval
**Date:** 2026-06-01
**Scope:** Locking the stack and base-repo conventions for the multi-tenant financial services platform. This is the day-0 reference. Once approved it becomes the scaffold spec and lives in `/docs`.

All versions below were verified against upstream release channels as of 1 June 2026. Sources are listed at the end.

---

## 1. How to read this

Section 2 is the locked stack with pinned versions and a one-line reason for each. Sections 3 to 11 cover the repo layout and the workflows that keep it honest. Section 12 lists the few choices I still want a call from you on. Section 13 is what I will actually generate once you approve.

Pinning stance: **fully modern, but production-stable**. Where a tool has a bleeding-edge release that is too fresh to bet a regulated platform on, I have said so and pinned the safe line instead. That distinction is called out explicitly so you can override it.

---

## 2. Locked stack

### Frontend

| Tool          | Pinned            | Why                                                                                                                               |
| ------------- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Node.js       | 24.x (Active LTS) | Current Active LTS. Node 26 is the live "Current" line but does not enter LTS until Oct 2026, too fresh for a platform of record. |
| pnpm          | 11.x              | Fast, disk-efficient, first-class workspace support. Pure ESM now, needs Node 22+, fine on 24.                                    |
| Svelte        | 5.56.x            | Runes reactivity, smaller and faster output. Stable and mature.                                                                   |
| SvelteKit     | 2.57.x            | File routing, server endpoints, form actions, remote functions, SSR where needed.                                                 |
| Tailwind CSS  | 4.x               | Current major, what shadcn-svelte targets.                                                                                        |
| shadcn-svelte | 1.3.x             | Now runes-native. Copy-in component source, no dependency lock-in, built on Bits UI.                                              |
| TypeScript    | 5.x latest        | Strict mode on, non-negotiable.                                                                                                   |

### Backend

| Tool          | Pinned  | Why                                                                                                                                |
| ------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Python        | 3.13.x  | Modern runtime with full ecosystem support. 3.14 exists but 3.13 is the safe production pick.                                      |
| uv (Astral)   | 0.8.x   | Single Rust-based tool for Python versions, venvs, deps and lockfile. 10-100x faster than pip, reproducible with `--frozen` in CI. |
| FastAPI       | 0.136.x | Async, OpenAPI generation built in, strong typing via Pydantic.                                                                    |
| Pydantic      | v2      | Validation and settings, the FastAPI standard.                                                                                     |
| SQLAlchemy    | 2.0     | Async ORM, the layer Alembic migrates.                                                                                             |
| Alembic       | latest  | De-facto migration tool for SQLAlchemy. RLS-aware flow in section 10.                                                              |
| ruff (Astral) | latest  | Lint + format in one Rust tool. Replaces flake8, isort, black, pyupgrade.                                                          |
| mypy          | latest  | Static type-checking. Astral's `ty` is emerging but still preview, so not betting on it yet.                                       |
| pytest        | latest  | Test runner.                                                                                                                       |
| psycopg       | 3.x     | Postgres driver, async-capable.                                                                                                    |
| WorkOS SDK    | 7.0.x   | Official Python SDK, ships `AsyncWorkOSClient` that drops straight into FastAPI's async handlers, fully typed.                     |

### Data & identity

| Tool             | Pinned  | Why                                                                                                                    |
| ---------------- | ------- | ---------------------------------------------------------------------------------------------------------------------- |
| PostgreSQL       | 18.x    | Latest major. Strong Row Level Security, the backbone of tenant isolation.                                             |
| WorkOS / AuthKit | current | Identity provider: email/password, magic links, passkeys, MFA, enterprise SSO, org management. App owns authorisation. |

### Monorepo & tooling

| Tool                            | Pinned | Why                                                                                                                                                                                |
| ------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| moon (moonrepo)                 | 2.2.x  | Task runner + monorepo orchestration, smart caching, affected-only runs. **Note:** v2 only landed May 2026 with a WASM-plugin toolchain rewrite. Modern but young, see section 12. |
| proto (moonrepo)                | latest | Pins Node, pnpm, Python versions per-repo so every dev and CI runner is identical.                                                                                                 |
| Hey API (`@hey-api/openapi-ts`) | latest | Generates the typed TS client from FastAPI's `openapi.json`. Consensus 2026 pick for FastAPI to TS.                                                                                |
| GitHub Actions                  | n/a    | CI/CD, with a self-hosted runner on Proxmox for deploy + migrations.                                                                                                               |

A note on frontend lint/format: I am keeping **ESLint (flat config) + Prettier** with the Svelte plugins rather than Biome. Biome is faster but its `.svelte` support is still weak in 2026, and broken Svelte linting is not worth the speed. Biome can lint the non-Svelte packages later if we want.

---

## 3. Repository structure

Moonrepo layout, pnpm workspaces for the JS side, uv workspace for Python.

**Application topology.** Three portals, three frontend deployments. The client portal and the platform portal (used by tenant staff — advisers, compliance, paraplanners) are both external and tenant-facing, so they ship as two public apps (`apps/client`, `apps/platform`). The admin portal is internal Graphene-only tenant management with effectively cross-tenant reach, so it ships as its own app (`apps/admin`) on a separate ingress that can be locked behind the corp VPN / IP allowlist (or Cloudflare Access / Tailscale). One FastAPI backend serves all three, with admin endpoints namespaced and reachable only from the internal network, not just guarded by an authz check. All three portals are auth-gated and share the `@graphene/auth` package; there is no self-service signup — accounts are provisioned in WorkOS.

```text
/
├─ apps/
│  ├─ client/              # SvelteKit: client portal (public)
│  ├─ platform/            # SvelteKit: platform portal for tenant staff (public)
│  ├─ admin/               # SvelteKit: Graphene tenant admin (internal, network-locked)
│  └─ api/                 # FastAPI: business logic, authz, audit, db
├─ pkgs/
│  ├─ api-client/          # generated TS client (Hey API) — do not hand-edit
│  ├─ auth/                # framework-agnostic WorkOS auth helpers
│  ├─ ui/                  # shared shadcn-svelte components
│  └─ config/              # shared tsconfig, eslint, prettier presets
├─ infrastructure/         # Coolify/compose definitions, deploy manifests
├─ docs/                   # this doc lives here
├─ .moon/                  # moon workspace + toolchain config, and the git hooks it generates
├─ .github/                # workflows, PR template, CODEOWNERS
├─ .prototools             # pinned Node/pnpm/Python versions
├─ pnpm-workspace.yaml
└─ pyproject.toml          # uv workspace root
```

Stateless apps, no data in containers. Build artefacts: `client`, `platform`, `admin`, `api`, plus `worker` and `scheduler` containers reserved for when background processing lands.

---

## 4. Toolchain & version management

`proto` pins every runtime version in `.prototools`, committed to the repo. New dev runs one command and gets the exact Node, pnpm and Python the CI runner uses, so "works on my machine" stops being a category of bug. `uv` pins Python deps in `uv.lock`, pnpm pins JS deps in `pnpm-lock.yaml`. CI installs with frozen lockfiles and fails if they drift.

---

## 5. Quality gates

Per language, run locally via moon tasks and enforced in CI.

Python: `ruff check` (lint) + `ruff format --check` (format) + `mypy` (types) + `pytest` (tests).

TypeScript/Svelte: `eslint` (lint) + `prettier --check` (format) + `svelte-check` and `tsc --noEmit` (types) + `vitest` (tests).

moon only runs these against **affected** projects on a given change, so PR feedback stays fast as the repo grows.

---

## 6. Git hooks (moon)

Declared in `.moon/workspace.yml` under `vcs.hooks`; `moon sync vcs-hooks` writes them to
`.moon/hooks/` and points `core.hooksPath` there, so `.git/hooks` is unused. `sync: true`
regenerates them on any moon command, and a fresh clone gets them from `pnpm install` (the root
`prepare` script) before a moon command has run.

- **pre-commit** — format the STAGED files and re-stage them (`scripts/hooks/format-staged.sh`:
  prettier for JS/TS/Svelte/JSON/CSS/MD, ruff format + `ruff check --fix` for Python). A lint finding
  ruff cannot fix stops the commit.
- **pre-push** — `scripts/hooks/pre-push.sh`: client drift, then `:lint :check :typecheck` in one
  `moon ci` call, then `:test`. Every moon step is `moon ci --base`, with the base set to where the
  branch left `origin/main`, so it checks what the branch affects, the same set CI checks, not the
  whole repo. Client drift runs only when the API, its Python packages, the client or a lockfile
  changed. The steps run in order and stop at the first failure; tests run after the static checks,
  not beside them, because some suites are timing-sensitive under load. ESLint keeps a per-file
  cache (`--cache`), so a re-lint reads only the files that changed.
- **commit-msg** — Conventional Commits on the subject line
  (`scripts/hooks/conventional-commit.sh`), skipping git's own generated merge/revert messages.

Hooks mirror CI so failures surface before the PR, not in the pipeline.

**This replaced lefthook** (August 2026), which needed a third-party binary, a pnpm build-script
allowlist entry, an install step, and a `.lefthookrc` shim to get proto's tools onto PATH — that last
one because lefthook generated `sh` scripts and the hooks aborted every commit under dash. moon
generates `bash` (`hookFormat: 'bash'`), so the shell question is settled by declaration. The PATH
export survives as the first entry of each hook, because a commit from a GUI or IDE still does not
source the interactive profile; the entries become lines in one script, so exporting once carries.

---

## 7. Commit conventions & branching

Conventional Commits (`feat:`, `fix:`, `chore:`, etc.), enforced at commit-msg. This gives clean history and the option of automated changelogs/versioning later.

Trunk-based with short-lived feature branches off `main`. `main` is protected: no direct pushes, PR + green CI + review required. CODEOWNERS routes reviews to the right people and is itself an access-control artefact for the SOC 2 story.

---

## 8. CI/CD pipelines

**Pull request** (GitHub-hosted runners):

1. Install with frozen lockfiles
2. Lint + format check (ruff, eslint, prettier)
3. Type-check (mypy, svelte-check, tsc)
4. Unit tests (pytest, vitest)
5. Build validation (all apps)
6. Security scans: `gitleaks` (secrets), `pnpm audit` + `uv`/`pip-audit` (deps), `trivy` (container/image)
7. OpenAPI client drift check (section 11)

**Main branch** (deploy via self-hosted Proxmox runner):

1. Build images
2. Migration check + run (section 10)
3. Deploy through Coolify

The self-hosted runner sits inside the private network so the database is never exposed and migrations run over a secure path.

---

## 9. Security & compliance hooks baked in from day 0

These are cheap now and expensive to retrofit, and map directly onto SOC 2 / ISO 27001 evidence:

- Secret scanning in CI (gitleaks) and dependency scanning every PR.
- CODEOWNERS + branch protection as documented access control.
- Conventional Commits + protected `main` as change-management evidence.
- Audit-log table gets INSERT-only grants for the app role from the first migration (ties into the earlier integrity decision: trusted-custodian model, but staff still cannot casually rewrite history).
- App DB role is non-superuser, non-owner, with `FORCE ROW LEVEL SECURITY` on tenant tables. The migration runner uses a separate elevated role.

---

## 10. Database migrations (Alembic, RLS-aware)

Expand/contract discipline: schema change ships and deploys **before** the code that depends on it, and every change is backwards-compatible with the running version so rollback is always safe.

Flow:

```text
Developer → PR → GitHub Actions (validate) → merge to main
  → self-hosted runner runs `alembic upgrade head` → deploy app
```

Two roles, deliberately separate: the **migration role** owns schema and can DDL; the **app runtime role** can only DML and is subject to RLS. Owners bypass RLS, so the app must never run as the table owner. Long operations (adding indexes, NOT NULL) use `CREATE INDEX CONCURRENTLY` and a sane `lock_timeout` so we never take a long lock on a live tenant table.

Every migration that creates a tenant-scoped table also creates its RLS policy in the same migration. RLS is not an afterthought you remember later, it ships with the table or the table does not ship.

---

## 11. OpenAPI to TypeScript type sync

Python/Pydantic is the single source of truth. FastAPI emits `openapi.json`, Hey API generates the typed client into `pkgs/api-client`, and the SvelteKit app imports from there. The frontend never hand-writes API types.

A CI step regenerates the client and fails the PR if the committed output differs from what the current schema produces. That makes API contract drift a build error instead of a runtime surprise, which closes the gap I flagged earlier where "shared-types" only covered the TS side.

---

## 12. Resolved decisions

1. **moon v2** — riding v2, greenfield so nothing to migrate.
2. **Schema** — _not_ included in this scaffold. The data model (including single vs multi adviser per client) is still open, so the backend ships the migration framework and RLS-aware role setup but no domain tables.
3. **Type checker** — mypy.
4. **Worker/scheduler** — left out for now. Container slots can be added when background processing actually lands.
5. **Portal split** — `apps/client` (client portal, public), `apps/platform` (platform portal for tenant staff, public) and `apps/admin` (internal, network-locked), see section 3.

---

## 13. What I will scaffold once you approve

- Moonrepo skeleton with the structure in section 3, `proto` + lockfiles pinned.
- `apps/client` and `apps/platform`: runnable SvelteKit 2 + Svelte 5 + Tailwind 4 + shadcn-svelte — the public client portal and the platform portal for tenant staff.
- `apps/admin`: separate runnable SvelteKit app for Graphene tenant admin, auth-gated (same `@graphene/auth` package and glue as the other portals) and ready to put behind the VPN/allowlist.
- `apps/api`: runnable FastAPI on uv, health endpoint, settings via Pydantic, WorkOS client wired but inert, SQLAlchemy + Alembic initialised with the two-role setup. **No domain schema** yet, per decision 2.
- `pkgs/api-client` with the generation script wired to the API's OpenAPI, `pkgs/ui` and `pkgs/config` stubbed.
- moon's git hooks, ESLint/Prettier/ruff/mypy configs, moon task definitions.
- `.github`: PR + deploy workflows, PR template, CODEOWNERS, conventional-commit check.
- Alembic migration flow with the expand/contract and RLS conventions documented inline.
- A root README explaining the one-command bootstrap.

---

## Sources

- [SvelteKit releases](https://github.com/sveltejs/kit/releases) · [Svelte releases](https://github.com/sveltejs/svelte/releases) · [What's new in Svelte: May 2026](https://svelte.dev/blog/whats-new-in-svelte-may-2026)
- [shadcn-svelte changelog](https://www.shadcn-svelte.com/docs/changelog) · [shadcn-svelte npm](https://www.npmjs.com/package/shadcn-svelte)
- [FastAPI release notes](https://fastapi.tiangolo.com/release-notes/) · [fastapi on PyPI](https://pypi.org/project/fastapi/)
- [uv docs](https://docs.astral.sh/uv/) · [ruff on PyPI](https://pypi.org/project/ruff/)
- [moon releases](https://github.com/moonrepo/moon/releases) · [moonrepo v2 announcement (InfoQ)](https://www.infoq.com/news/2026/05/moonrepo-2-release/)
- [pnpm releases](https://github.com/pnpm/pnpm/releases) · [Node.js releases](https://nodejs.org/en/about/previous-releases)
- [PostgreSQL release notes](https://www.postgresql.org/docs/release/) · [PostgreSQL 18.4 announcement](https://www.postgresql.org/about/news/postgresql-184-1710-1614-1518-and-1423-released-3297/)
- [WorkOS Python SDK](https://github.com/workos/workos-python) · [Securing a FastAPI server with WorkOS AuthKit](https://workos.com/blog/securing-a-fastapi-server-with-workos-authkit)
- [FastAPI: generating clients](https://fastapi.tiangolo.com/advanced/generate-clients/) · [Hey API / openapi-ts](https://openapi-ts.dev/)
