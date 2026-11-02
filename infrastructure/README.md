# Infrastructure & deployment

Each app builds a stateless container; persistent state lives in PostgreSQL and object storage only.

## Services

- `client` — public client portal (public ingress)
- `platform` — public platform portal for tenant staff — advisers, compliance, paraplanners (public ingress)
- `admin` — internal tenant admin (**private ingress**, locked behind corp VPN / IP allowlist); auth-gated like the other portals, so it also takes the WorkOS env
- `api` — FastAPI backend (portal API, `:8000`)
- `external` — firm API (Litestar, `python -m external` on the `api` image, `:8010`). Same image as `api`, a different command. Firms call it directly, so give it a public domain. `EXTERNAL_STUB` is `false` here unless the environment already sets it.

The admin ingress **and** the admin API surface must be reachable only from the internal network. Locking the admin frontend alone is not sufficient — the tenant-management API endpoints must be network-restricted too, not merely guarded by an authz check.

Build artefacts today: `client`, `platform`, `admin`, `api`. `worker` / `scheduler` slots are reserved for when background processing lands.

## Deploy pipeline (`.github/workflows/deploy.yml`)

Triggered on push to `main` (i.e. after a PR merges). Runs on a **self-hosted runner on Proxmox** that sits inside the private network, so the database is never exposed and migrations run over a secure path. `concurrency: deploy-main` with `cancel-in-progress: false` serialises deploys.

Steps, in order:

1. Checkout, install toolchain (moon/proto), `pnpm install --frozen-lockfile`, `uv sync --frozen`.
2. **Migrate first** — `moon run api:migrate` (`alembic upgrade head`) using the elevated migration role. Migrations are expand/contract, so this is safe against the currently-running app (see [`../docs/stack-and-workflows.md`](../docs/stack-and-workflows.md) §10).
3. `moon run :build` — build all app images (`client`, `platform`, `admin`, `api`).
4. `curl -X POST "$COOLIFY_DEPLOY_WEBHOOK"` — trigger the Coolify deploy.

**One exception to expand/contract, and what it needs:** `0146_orgs_are_nodes` RENAMES a table, four columns and an enum type, with no shim (its header says why, and names the precedent on `main`). No API router reads the renamed columns, so the running app is unaffected — but `pkgs/book-sink` names them directly, so a **provider-pipeline container still running the previous image** fails its merge for the whole tenant and files it as "unavailable", which reads as "this firm has no data with this custodian". The images are all built and pushed before the migration runs and the release is triggered after it, so the ordering is already right; the window to watch is a chain firing on its cron between the migration and the pipeline containers restarting. Let a mid-run chain finish, or expect one night's rejects.

Note the ordering trade-off: migrations apply before the build. Because changes are backwards-compatible, a failed build leaves a migrated DB still serving the old app — acceptable under expand/contract, but there is no automatic rollback/notification step yet.

## Required CI secrets

Set on the **deploy** workflow (repo/environment secrets):

| Secret                   | Used for                                        |
| ------------------------ | ----------------------------------------------- |
| `MIGRATION_DATABASE_URL` | Alembic connects as the elevated migration role |
| `COOLIFY_DEPLOY_WEBHOOK` | Coolify deploy trigger URL                      |

The PR workflow needs no deploy secrets; its `security` job runs gitleaks (full history) + trivy, and `client-drift` regenerates the API client. CI/quality-gate detail lives in [`../docs/stack-and-workflows.md`](../docs/stack-and-workflows.md) §8.

## Runtime configuration

Each service is configured by environment variables, never committed secrets (only `.env*.example` is tracked):

- `api` — see [`../docs/api.md`](../docs/api.md#configuration). In production the app connects with the **DML-only, RLS-bound** role; only the deploy migration step uses the elevated role.
- `external` — the same `DATABASE_URL`. `EXTERNAL_HOST` is `0.0.0.0`, `EXTERNAL_PORT` is `8010`, and `EXTERNAL_STUB` defaults to `false` in this stack (`${EXTERNAL_STUB:-false}`, so a value already set in the environment wins). `SCREENING_API_KEY` is the screening provider key for a live check; blank fails that call closed. `SCREENING_WEBHOOK_SECRET` verifies the inbound screening webhook; blank refuses it. `SECRETS_MASTER_KEY` is required, the same Fernet key as `api` and the worker; blank fails `PUT /webhook` closed. Local `python -m external` leaves `EXTERNAL_STUB` at its process default, `true`.
- `client` / `platform` / `admin` — WorkOS keys (`WORKOS_CLIENT_ID`, `WORKOS_API_KEY`) + `WORKOS_COOKIE_PASSWORD` (seals the session cookie) + `API_URL`; see [`../docs/auth.md`](../docs/auth.md#environment). All three portals are auth-gated — `admin` is now wired to the same `@graphene/auth` stack and so needs the same WorkOS env as the public portals (it previously had none); it stays network-locked on top.
- `intelliflo-scheduler` / `intelliflo-tenant-sync` — two of three deployables from one Dockerfile (`services/integrations/intelliflo`, one build target each, pushed as separate images — unlike `seccl`'s one image + compose `command:` override). Read-only against the shared SEI lake (`DATALAKE_CATALOG_DSN` as `lake_reader`); writes only to its own `integrations.*` schema via its own `INTELLIFLO_PG_*`-sourced login. Both are plain cron loops with no healthcheck to probe yet. The Dockerfile's third target, `webhook` (WebSub receiver, `:4000`), is not built or deployed — not needed today.

### One variable per fact

Coolify gives the whole stack **one** environment list, so anything several services need is set
there **once** and fanned out by `docker-compose.yaml` — never asked for twice under two names.
The `x-*` anchors at the top of that file are the fan-out (an S3 credential, a WorkOS app, a Seccl
login, an observability block, a ComplyCube account), and a handful of per-service names are
**derived** and must NOT appear in the list:

| Derived name                                                                                                             | Comes from                                           |
| ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------- |
| `SECCL_S3_ENDPOINT` / `_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY` / `_BUCKET`                                                | the one `S3_*` credential                            |
| `SECCL_WAREHOUSE_DSN`                                                                                                    | `DATALAKE_WRITER_DSN`                                |
| `SECCL_WAREHOUSE_DATA_PATH`                                                                                              | `S3_CORE_BUCKET`                                     |
| `ALLOWED_ORIGINS`, `OPERATIONS_PORTAL_URL`, `CLIENT_PORTAL_BASE_URL`, `SECCL_WEBHOOK_URL`                                | Coolify's own `SERVICE_URL_*` (the "Domains" fields) |
| `API_URL`, `RENDER_SERVICE_URL`, `INTERLACE_SECCL_URL`, `GATEWAY_INTERLACE_URL`, `SECCL_STATE_PATH`, `SECCL_STREAM_PATH` | internal compose hostnames and volume paths          |

Setting one of those by hand is how a list grows two spellings of one secret, then drifts.

**Leave an optional var OUT rather than setting it blank.** A blank value and an absent one behave
identically (compose's `:-` treats both as unset), but a list of thirty blanks hides which twelve
values this deployment actually decides.

### Uploads: the portals' request-body ceiling

`@sveltejs/adapter-node` caps a request body at **512 KB** by default (`env('BODY_SIZE_LIMIT', '512K')`
in its `handler.js`) and enforces it as the body is READ — before any form action runs and with no way
for one to intervene. `vite dev` has no such ceiling, so **every upload works locally and 413s on a
deployment**, and the failure is SvelteKit's own bare `Payload Too Large`, which names neither a limit
nor a file. It is the whole REQUEST that counts, so a multi-file drop is measured as one body.

`docker-compose.yaml`'s `x-portal` anchor therefore sets `BODY_SIZE_LIMIT` (default `256M`) for all
three portals. It is sized ABOVE the API's own multi-file ceiling — `documents.py::MAX_UPLOAD_BODY_BYTES`,
25 MiB x 10 files + 1 MiB headroom = 251 MiB — so an over-size drop is refused by the API, whose message
quotes the real limit. Setting it below that just moves the refusal back to the anonymous one.

Everything the browser POSTs to a portal crosses it, not only the document library: supporting files,
client request fulfilment, chat attachments (the UI promises 25 MiB) and a branding logo near its own
512 KiB cap. The cost of the larger ceiling is memory — a portal's form action calls `request.formData()`,
which buffers the whole multipart body before forwarding it to the API — so lower it if the portal
containers are tightly sized, and accept that large drops fail with the API's message instead.

No test can catch this: it is a property of the deployment's environment, not of the code.

### The bundled object store

Documents (client uploads and rendered PDFs) need real S3-compatible storage — there is no
data-URI fallback. `docker-compose.yaml` bundles one (`rustfs` + the one-shot `createbucket`) so a
deployment can run the whole upload → store → view path with no external dependency. To use an
external store instead, set `S3_ENDPOINT_URL` + `S3_ENDPOINT_HOST` + the credential pair; the
bundled pair then runs as idle spares. `createbucket` never sees that external endpoint.
Prod's `deploy-prod` workflow creates `portals-documents`, `S3_CORE_BUCKET`, and
`integrations` on `s3-prod` with `rustfs/rc` before `app_stack` starts the pipelines.
The object-store secret is not a GitHub Environment value — `render_app_env.py` drops
it — so that step reads `vault_portals_s3_secret_key` from the infra vault.

**Do not gate them behind a compose profile.** They were, for two days (`d3b45267`, so that the
external-S3 deploys would skip them cleanly), and the tidiness was not worth what it cost. A
profiled service is absent from a plain `docker compose up` — which is exactly what Coolify runs,
and there is no `--profile` to pass it — so dev lost its object store, while `S3_ENDPOINT_URL` went
on defaulting to `http://rustfs:9000` and left a completely valid configuration aimed at a service
that no longer existed.

That state is silent in every way that matters. Nothing fails at boot, every container is healthy,
the portals serve every page, and only the paths touching object storage break — an upload hangs
and then errors, a render job never produces a PDF. It reached clients as "That didn't work" on an
onboarding document upload, and it survived several green deploys before anyone connected the two.

Two things now make it loud if it recurs: `/health/ready` reports `documents_storage: down` (the
probe head-buckets the documents bucket), and the deploy workflow's smoke test **fails on any probe
reporting `down`** instead of accepting `degraded`.

### Secrets are values, not files

Two credentials used to need a file on the deployment host, because Coolify **refuses a bind-mount
source containing `${`** (variable substitution in a volume source is a command-injection vector,
and the deploy fails at import with "Invalid volume source"). Both now take the bundle base64-encoded
in the environment instead, so the stack has no host-side prerequisite at all:

| Credential                     | Variable                | Notes                                                                                 |
| ------------------------------ | ----------------------- | ------------------------------------------------------------------------------------- |
| Seccl PEM (`private_key_jwt`)  | `SECCL_PRIVATE_KEY_B64` | `private_key_b64` wins over `private_key_path` in `seccl_sync.settings`               |
| SEI PKCS#12 client certificate | `SEI_PFX_BASE64`        | The only form — `SEI_PFX_PATH` was removed Aug 2026 (one secret, one precedence rule) |

`base64 -w0 seccl.pem` / `base64 -w0 graphene.pfx` produces the value. A newline-wrapped value
(what a textarea gives you) is tolerated on both. The Seccl decoder also restores a trailing
`=` when a form has dropped it: without that character Python raises `Incorrect padding`, and
the pipeline reports it as `raw.accounts` failing at startup. The production value is the
GitHub `prod` Environment secret `SECCL_PRIVATE_KEY_B64`, not a file in the repo.

## Container images & compose

Images are built from the repo root (the pnpm/uv workspaces live there):

- [`../apps/api/Dockerfile`](../apps/api/Dockerfile) — uv → uvicorn, multi-stage, non-root, `:8000`. The `external` service runs this same image as `python -m external` on `:8010`.
- [`../apps/client/Dockerfile`](../apps/client/Dockerfile) / [`../apps/platform/Dockerfile`](../apps/platform/Dockerfile) / [`../apps/admin/Dockerfile`](../apps/admin/Dockerfile) — pnpm build → SvelteKit adapter-node (`node build`), multi-stage, non-root, `:3000`.
- [`../docker-compose.yaml`](../docker-compose.yaml) — wires all four for Coolify's Docker Compose build pack.

Build/run locally to validate before relying on Coolify:

```bash
docker compose build
# SERVICE_URL_* are auto-set by Coolify in production; supply them by hand only for a local run.
SERVICE_URL_CLIENT=http://localhost:3000 SERVICE_URL_PLATFORM=http://localhost:3001 SERVICE_URL_ADMIN=http://localhost:3002 \
DATABASE_URL=... WORKOS_CLIENT_ID=... WORKOS_API_KEY=... WORKOS_COOKIE_PASSWORD=... \
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001 docker compose up
```

(Or build one image directly: `docker build -f apps/api/Dockerfile -t graphene-api .`)

## Deploying on Coolify

1. **Database first.** Create the Postgres resource (Coolify-managed or external) on the private network, with the two roles — `app` (DML-only, RLS-bound) and the elevated migration role. This stack does **not** contain Postgres; migrations run from CI (`deploy.yml`) before the app is released.
2. **New resource → Docker Compose**, pointed at this repo; base/compose path = repo root (`docker-compose.yaml`). Coolify builds the four images.
3. **Set env vars** in Coolify — the canonical list is below. Fifteen are **required** (compose's
   `:?` modifier fails the deploy fast if one is missing); everything else has a working default.
   **Don't paste the `:?` placeholder text as a value.** Coolify may seed a var with the message
   after `:?` (e.g. `DATABASE_URL=set in Coolify`); that text is a reminder, not a value. The api
   passes `/health` even with junk secrets, so a mis-seeded value deploys "green" and fails at the
   first DB, auth or vendor call — exactly the failure that is hardest to attribute.

   **Required (21).** `DATABASE_URL` (app role) · `MIGRATION_DATABASE_URL` (elevated, worker only) ·
   `WORKOS_CLIENT_ID` · `WORKOS_API_KEY` · `WORKOS_COOKIE_PASSWORD` (**≥32 chars** — the WorkOS SDK
   rejects shorter and login throws) · `INTERNAL_API_TOKEN` · `SECRETS_MASTER_KEY` (Fernet) ·
   `S3_SECRET_ACCESS_KEY` · `DATALAKE_WRITER_DSN` (libpq, `lake_writer`) · `SECCL_API_URL` ·
   `SECCL_FIRM_ID` · `SECCL_AUTH0_DOMAIN` · `SECCL_CLIENT_ID` · `SECCL_WEBHOOK_SECRET` ·
   `DATALAKE_CATALOG_DSN` (libpq, `lake_reader` — intelliflo-sync's read of the shared SEI lake;
   blank ⇒ all three intelliflo-\* services fail to start) · `INTELLIFLO_CLIENT_ID` ·
   `INTELLIFLO_CLIENT_SECRET` · `INTELLIFLO_API_KEY` · `INTELLIFLO_PG_HOST` · `INTELLIFLO_PG_USER` ·
   `INTELLIFLO_PG_PASSWORD` (intelliflo-sync's own login to the `integrations` schema — same
   Postgres instance as `DATABASE_URL`, a narrower role) · plus `APP_STAGE` for anything that isn't
   production.

   **Worth setting** (defaults exist but are rarely what you want): `ALLOWED_ORIGIN_REGEX` (the
   host-keyed portals' origins) · `SYSTEM_USER_IDS` · `RESEND_API_KEY` + `EMAIL_FROM` (+ `RESEND_WEBHOOK_SECRET` once a delivery endpoint exists in Resend — blank just means delivery states arrive via the poll instead, about a minute late, via `COMMS_POLL_SECONDS`) ·
   `S3_CORE_BUCKET` · `DATALAKE_DATABASE_URL` (blank ⇒ no lake for the **api**/**render** services —
   intelliflo-sync doesn't read this one) ·
   `SECCL_PRIVATE_KEY_B64` · `POSTCODER_API_KEY` on the **api** service (blank ⇒ address lookup
   503s; the portals' `/api/postcoder` routes are thin BFFs that forward to the API) ·
   `CTC_ENABLED` + `CTC_AGENT_NAME` (blank ⇒ illustrations file a marked PLACEHOLDER, not a
   quotation) · the `MORGAN_LLOYD_*` set (blank ⇒ no drawdown session, no CRM sync) · the `SEI_*`
   set (blank ⇒ a booked contribution files locally but lodges no Anticipated Cash Receipt) ·
   `SCREENING_API_KEY` + `SCREENING_WEBHOOK_SECRET` (blank ⇒ identity and AML checks stay a manual
   gate, the AML console answers 503, and the screening webhook is refused) · the rest of the
   `INTELLIFLO_*` set (`_APP_ID`, `_WEBHOOK_SECRET`, the per-feature `_*_CRON`
   overrides — blank ⇒ that one feature/cron falls back to its own default, not a hard failure).

   Coolify's `SERVICE_URL_*` / `SERVICE_FQDN_*` vars are generated from the "Domains" fields in
   step 4 — **don't set them by hand**, and delete any left over for a service that no longer
   exists (a `SERVICE_URL_MINIO` outlives the MinIO this stack dropped, and reads like config).

4. **Domains & ingress.** Give `client` and `platform` their public domains. The browser calls only
   its portal origin: REST and the long-lived realtime SSE responses are both proxied server-side
   to `http://api:8000`, so the API needs **no browser-facing domain**. Keep `api` and `admin` on the
   corporate network. A vendor webhook still needs a deliberately narrow public ingress for its
   exact route; do not publish the monolithic API merely to receive one callback. Lock `admin` to
   the corp VPN / IP allowlist / Cloudflare Access / Tailscale (see the network-locking note above).
5. The repo's [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) runs migrations then `POST`s `COOLIFY_DEPLOY_WEBHOOK` — so a push to `main` migrates, then triggers this stack's rebuild/release.

Healthchecks are defined per service (`/health` for api and external, `/` for client/platform/admin) so Coolify waits for readiness; `client`/`platform`/`admin` start only after `api` is healthy. `external` does not: firms call it on its own domain, and the portal API does not proxy it.

**Build parallelism (OOM guard).** Coolify builds all five images with one `docker compose build`,
which runs them **concurrently** — three Vite builds + two uv installs at once. On a modest server
the kernel OOM-kills the build container and the deploy dies with **exit code 255 and no error
line** (the log just stops mid-build). Fix: add two env vars to the app in Coolify (tick
**Build Variable** on each) so the images build one at a time:

```
COMPOSE_PARALLEL_LIMIT=1
COMPOSE_BAKE=false
```

`COMPOSE_BAKE=false` matters — newer Compose delegates to buildx bake by default (the log shows
`load local bake definitions`), and bake ignores `COMPOSE_PARALLEL_LIMIT`. Both are set on the
current GPLDOCFRA01 deployment.

## Object storage (S3)

Public white-label assets (tenant logos/favicons) live in an **external** S3-compatible bucket (AWS S3 / Cloudflare R2 / Backblaze B2) — the stack no longer ships an in-stack MinIO. Provision the bucket, make it publicly readable (object ACL or bucket policy), and set the `api` service's `S3_*` env in Coolify: `S3_ENDPOINT_URL` (blank => AWS default for the region), `S3_BUCKET`, `S3_ACCESS_KEY_ID`/`S3_SECRET_ACCESS_KEY`, and `S3_PUBLIC_BASE_URL` (the browser-reachable base, including the bucket, e.g. `https://assets.example.com/branding`). Left blank, uploads fail closed and branding falls back to pasting a hosted asset URL directly.

Private documents (uploads + rendered PDFs) are separate: they live in `S3_DOCUMENTS_BUCKET`, defaulting to the stack's bundled `rustfs` service (see `docker-compose.yaml`) so the document pipeline works out of the box; point the same `S3_*` credentials at an external store to move both buckets off-stack.

Local development uses `docker-compose.local.yaml` (Postgres 18 + RustFS; S3 API `:9000`, console `:9001`, creds `rustfsadmin`). `moon run api:db-up` / `pnpm db:up` own the db+store lifecycle and create the external `graphene-dev` network first (`scripts/ensure-graphene-dev-network.mjs`). A full `docker compose -f docker-compose.local.yaml up` also starts the API, portals, pipelines and worker. Warehouse DSNs inside containers are `db` / `rustfs`; app secrets come from each project's env_file, not the root `.env`.

## Not yet built

The audit-log store is still design-only (see [`../docs/project-context.md`](../docs/project-context.md)). No `worker`/`scheduler` images yet.
