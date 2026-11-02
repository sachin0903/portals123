# Morgan Lloyd — Operations

## Where the integration runs

`services/api` (Python FastAPI, port 3002) — the platform-adapter service. Morgan Lloyd is a new adapter under `app/platforms/morgan_lloyd/` (alongside `sei/`). Durable work runs through **PGMQ** queues consumed by workers; the FastAPI lifespan starts the worker(s) and the scheduler.

> ⚠️ The `services/api` Python **source is currently not on disk** (only `.pyc` bytecode; `services/` is untracked in git). Restore/commit the source before any work — see the implementation plan's "Blocking prerequisite".

## Components to build (none exist today)

| Component            | Path (new)                                        | Purpose                                                                                                                 |
| -------------------- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| ML API client        | `app/platforms/morgan_lloyd/client.py`            | Azure AD token cache/refresh; OData GET/POST-action/PATCH helpers; `@odata.bind`; error parsing; retry/backoff          |
| ML adapter           | `app/platforms/morgan_lloyd/adapter.py`           | implement `submit_application`/`poll_process`/`fetch_*` (today: stub)                                                   |
| ML mapping           | `app/platforms/morgan_lloyd/mapping.py`           | Graphene graph → ML payloads (see data-mapping.md)                                                                      |
| Scheduler            | `app/workers/scheduler.py`                        | cron → enqueue PGMQ messages (`ml_valuations`, `ml_contributions_poll`, `ml_recon`, `ml_sftp_ingest`)                   |
| Generic queue worker | `app/workers/queue_worker.py`                     | `QueueWorker(queue, handler)` reusing the `SyncWorker` shape                                                            |
| SFTP ingest          | `app/integrations/sftp.py`                        | poll/receive ML documents → MinIO → `onboarding.documents` → event                                                      |
| CTC client           | `app/integrations/ctc/{client.py,xml_builder.py}` | **built** — bespoke XML v1.6 request → PDF. No credentials (`nodb=true`; identified by `<agent_name>`)                  |
| ML benefits/drawdown | `app/integrations/morgan_lloyd/benefits.py`       | **built** — `cam_setmemberdetails` → CTC1 `illustrationTemplate` + one-time DCube `ssoUrl`. Needs `MORGAN_LLOYD_*` only |

New DB tables (Drizzle, `pkgs/db/src/schema.ts` → `db:generate`/`db:push`): `beneficiaries`, `transfers`, `contributions`, `policy_valuations`, `benefit_applications`, `reconciliation_breaks`.

## Configuration (extend `Settings` in `app/config.py`)

Today `Settings` has only `sei_*`. Add (all from env / secret store):

```
ml_enabled, ml_base_url, ml_tenant_id, ml_client_id, ml_client_secret,
ml_resource (Dynamics org URL), ml_scheme_type=16, ml_sipp_type=809130016,
ml_provider_id=6ac39921-d8b7-f011-bbd3-7ced8d6b85ff,
ml_sftp_host, ml_sftp_user, ml_sftp_key_path, ml_sftp_base_path
```

## Secrets & security

- **ML Client Secret** is delivered separately/encrypted (`.morgan-lloyd-key` is present and untracked) — keep it in a secret manager; **never commit**. Same for the SEI creds/certs under `services/api/certs/` and `.env.local` — verify they are git-ignored, and **rotate anything that has been exposed in plaintext**.
- **Document storage must not be public.** Commit `4f5dab6 "make storage public"` is a temporary fix that is unacceptable for FCA pension documents (illustrations, KFIs, drawdown packs). Restore signed-URL access via `pkgs/storage/file-proxy.ts` before storing ML documents.
- **Retention/audit:** FCA SYSC — retain illustrations/statements ≥6 years; keep an immutable audit log (who / when / which version / correlation id) for every external call.
- **PII in transit:** NINO, DOB, bank details reach ML and CTC — enforce TLS, least-privilege credentials, and per-saga correlation ids.

## Scheduler cadence

| Job                        | Cadence                                     | Queue                   |
| -------------------------- | ------------------------------------------- | ----------------------- |
| Daily valuations push      | each business day, after analytics complete | `ml_valuations`         |
| Contribution / RAS polling | hourly (business hours)                     | `ml_contributions_poll` |
| Transfer status polling    | daily                                       | (transfers handler)     |
| SFTP document ingest       | every N minutes                             | `ml_sftp_ingest`        |
| Three-way reconciliation   | daily                                       | `ml_recon`              |

## Ops console (admin UI, `apps/platform`)

Surface: sync-job / saga state, manual-intervention queue, contribution-confirmation queue, drawdown-event queue, polling health, reconciliation breaks (with ageing), and audit search by client / correlation id / ML id / SEI id.

## Smoke tests

1. **Auth:** with ML dev creds, obtain an Azure AD token and `GET /WhoAmI` → 200 (mirror `scripts/check_sei_auth.py`).
2. **Valuations (Phase 1):** seed a linked ML account, run a scheduler tick → assert a `policy_valuations` row, a `cam_createpolicyvaluation` call (record `mlValuationId`), idempotent re-run, and read-back via `cam_getschemebyschemref`.
3. Run `ruff` / `mypy` / `pytest` (Python) and `pnpm check-types` / `pnpm lint` (TS) for each change.
