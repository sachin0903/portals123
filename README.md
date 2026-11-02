
Multi-tenant financial services platform. Monorepo managed by [moon](https://moonrepo.dev) (v2) and [proto](https://moonrepo.dev/proto).

See [`docs/stack-and-workflows.md`](docs/stack-and-workflows.md) for the full stack and engineering-workflow reference.

## Layout

```text
apps/client   SvelteKit — client portal: end users (public)
apps/platform SvelteKit — platform portal: tenant staff — advisers, compliance, paraplanners (public, role-based)
apps/admin    SvelteKit — Graphene internal admin: ops, RMs (network-locked, auth-gated)
apps/api      FastAPI   — business logic, authz, audit, database
pkgs/*    shared TS config, UI components, auth helpers, generated API client
infrastructure deploy manifests (Coolify)
docs          architecture & workflow docs
```

## Bootstrap

```bash
proto use               # install the toolchain (Node, pnpm, Python, uv) at the exact pinned versions
moon run :install       # uv sync + pnpm install
```

## Day-to-day

```bash
moon run :dev           # run all dev servers
moon run client:dev     # just the client portal (:5173)
moon run platform:dev   # just the platform portal (:5175)
moon run admin:dev      # just the admin portal (:5174)
moon run api:dev        # just the API (:8000)
moon run :check :lint :test   # what CI runs
```

## Generate the typed API client

With the API running locally:

```bash
moon run api-client:generate
```

This reads the API's `openapi.json` and regenerates `pkgs/api-client`. CI fails if the committed client is out of date. See [`docs/packages.md`](docs/packages.md) for details.

## Docs

- [`docs/stack-and-workflows.md`](docs/stack-and-workflows.md) — stack, pinned versions, engineering-workflow rationale
- [`docs/project-context.md`](docs/project-context.md) — background, decisions, next steps
- [`docs/auth.md`](docs/auth.md) — WorkOS auth + MFA flow
- [`docs/api.md`](docs/api.md) — FastAPI backend: endpoints, config, DB roles, RLS context
- [`docs/packages.md`](docs/packages.md) — shared packages: generated client, auth helpers, shared configs, ui
- [`docs/delivery.md`](docs/delivery.md) — engineering delivery: risk-class gating, Jira/Confluence conventions, the global DoD
- [`infrastructure/README.md`](infrastructure/README.md) — services + deploy pipeline

## Notes

- `apps/admin` is intended to deploy on a separate ingress locked behind the corp VPN / IP allowlist. Do not merge it into the public apps. The shared auth stack lives in `pkgs/auth` and is wired into all three portals (`client`, `platform`, `admin`). **No portal has self-service signup** — accounts are provisioned in WorkOS; the portals expose login/MFA/verify/logout/security-settings only.
- The backend ships the migration framework and RLS-aware DB roles but **no domain schema** yet — the data model is still being decided.
- moon v2 shipped May 2026; if a `moon run` task misbehaves, the underlying `package.json` scripts and `uv run` commands are runnable directly and are the source of truth.
