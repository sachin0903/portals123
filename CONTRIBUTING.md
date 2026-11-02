# Contributing

How we work in this repo. `CLAUDE.md` is the canonical orientation + standards; this is the human-facing summary. British English in prose, comments and docs.

## Setup

```bash
proto use                      # pinned Node, pnpm, Python, uv, moon
pnpm install                   # JS deps + git hooks (moon sync vcs-hooks)
uv sync --project apps/api     # Python deps
```

See [`README.md`](README.md) for day-to-day commands and the [`docs/`](docs/) index for architecture.

## Quality gates

Everything must pass before a change is "done":

```bash
moon run :lint :format :check :typecheck :test
```

Note the task-name split: TS type-checking is under `check` (svelte-check, client/platform/admin), Python under `typecheck` (mypy, api) — so the full command above is the real gate; `:typecheck` alone skips the frontends. moon only runs against affected projects, so this stays fast.

Don't weaken a gate to make it pass — no skipped tests, `--no-verify`, disabled lint rules, or loosened types — without explicit agreement, and say so plainly if you do.

## Tests ship with the code

Non-trivial logic gets unit tests **in the same change**. A bug fix adds a test that fails before the fix and passes after.

- **TS/Svelte** — co-locate `*.test.ts` next to the source; run `moon run client:test` / `platform:test` / `admin:test` (vitest). Vitest picks up the `sveltekit()` plugin from `vite.config.ts`, so `$lib`/`$env`/`$app` resolve in tests. Framework-agnostic logic lives in `@graphene/auth` and is tested with plain vitest (no SvelteKit) — examples: `pkgs/auth/src/auth.test.ts`, `errors.test.ts`, `validation.test.ts`.
- **Python** — `apps/api/tests/test_*.py`; run `moon run api:test` (pytest). Examples: `tests/test_auth.py` (JWT verification), `tests/test_db.py` (RLS fail-closed). `mypy` checks `app/` only; `ruff` lints `tests/` too.

Security-critical logic (auth, sessions, RLS context, token/error parsing) is not optional to test.

## Commits & branches

- **Conventional Commits** (`feat:`, `fix:`, `chore:`, …), enforced by the commit-msg hook.
- Trunk-based: short-lived branches off `main`; `main` is protected (PR + green CI + review).
- Git hooks (moon's own, declared in `.moon/workspace.yml` under `vcs.hooks` and generated into
  `.moon/hooks/`): pre-commit formats/lints the staged files and re-stages them; pre-push runs
  lint/check/typecheck/test on the projects the branch affects (`moon ci`, like CI). Hooks mirror CI
  so failures surface early.

## Verify external API shapes

WorkOS/SDK error and response shapes have bitten us before (see the MFA flow in [`docs/auth.md`](docs/auth.md)). Confirm field names against the installed SDK types or a real response, then pin the finding in a test — don't assume.

## Secrets

Only `.env*.example` is tracked. Never commit real `.env`/`.env.local`, and never log tokens, sealed sessions, or pending-auth tokens. gitleaks scans every PR.

## Generated API client

`pkgs/api-client` is generated from the API's OpenAPI schema — never hand-edit it. After changing an API route or model, regenerate and commit it (CI fails on drift). See [`docs/packages.md`](docs/packages.md).
