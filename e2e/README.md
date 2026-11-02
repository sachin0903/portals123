# E2E (Playwright, Python)

Real Chromium driving the running dev servers. Uses the `playwright` package already in the API's
deps (no new dependency); tests are plain `pytest` + `playwright.sync_api`.

## Run the dev servers

```
moon run api:migrate                 # once (schema current, incl. flows)
uv run --directory apps/api python scripts/seed_onboarding_flow.py graphene   # once
moon run api:dev platform:dev client:dev
```

## Run the tests

```
cd <repo>
uv run --project apps/api pytest e2e/ -q
```

Without any captured login, only the **public** suite (`test_public.py`) runs; the authenticated
suites skip.

## Capturing a login (storageState) for the authenticated suites

The portals authenticate through WorkOS, so you log in once yourself and hand the session to
Playwright as a `storageState` file (no credentials are stored in the repo or shared):

```
# Platform portal
uv run --project apps/api python -m playwright open --save-storage=platform-state.json http://localhost:5175
#   → log in in the window that opens, then close it.

# Client portal
uv run --project apps/api python -m playwright open --save-storage=client-state.json http://localhost:5173
#   → log in, close.
```

Then point the tests at them and run:

```
E2E_PLATFORM_STATE=$PWD/platform-state.json \
E2E_CLIENT_STATE=$PWD/client-state.json \
uv run --project apps/api pytest e2e/ -q
```

Base URLs default to `:8000` (API), `:5175` (platform), `:5173` (client); override via `API_URL`,
`PLATFORM_URL`, `CLIENT_URL`.

## Scope / safety

The suites deliberately **skip side-effecting flows** (no case _invite_, no public `/start`
_submit_) — those create a real WorkOS user + send an email. Flow authoring, starting a case, and
setting up an account are DB-only and safe to run against a dev DB.

The identity capture suite (`test_client_identity_capture.py`) takes the photos with Chromium's fake
camera but does not submit them, because a submit starts a ComplyCube identity check. Set
`E2E_COMPLYCUBE_SUBMIT=1` to submit as well, against a sandbox key only.
