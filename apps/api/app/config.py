import json
import re
from typing import Annotated

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # Read .env then .env.local (local values win), matching the Vite convention.
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    # Application DB role: DML only, subject to row level security.
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/portals"
    # Elevated role used ONLY by Alembic migrations (owns schema, can DDL).
    migration_database_url: str = "postgresql+psycopg://migrator:migrator@localhost:5432/portals"

    # Which deployment stage this instance is: 'dev' | 'staging' | 'production'. One stage per
    # deployment (dev/staging/prod are separate instances, each its own DB), so a tenant has a
    # single WorkOS environment row here, stamped with this stage. Drives tenant-config writes.
    app_stage: str = "production"

    # DEV/DEMO-ONLY auth bypass for E2E/local testing (reuses the existing DEMO_AUTH_BYPASS env).
    # When true AND app_stage != 'production', the API accepts a `dev:<base64url-json>` bearer token
    # and builds the Principal from it WITHOUT WorkOS JWKS verification (mirrors the portals'
    # hooks.server.ts bypass, which is cookie-gated so the unauthenticated state stays testable).
    # Off by default; the production guard means flipping it on a prod deploy still refuses.
    # NEVER enable in production.
    demo_auth_bypass: bool = False

    # Feature flag for the generic orchestration engine (flows.executions + polymorphic-subject
    # cases + generic state machine + crm.applications projection). Off by default: the current
    # single-case flow engine stays authoritative until parity is reached and this is flipped on.
    flows_orchestrator_v2: bool = False

    # ---- Rate limiting (app/ratelimit.py) --------------------------------------------------
    # Fixed-window caps on the UNAUTHENTICATED surfaces. Every limit is "requests per window
    # per key"; 0 disables that individual limit, and `rate_limit_enabled=false` disables the
    # lot (tests, and a break-glass switch). See app/ratelimit.py for the key each one uses.
    rate_limit_enabled: bool = True
    # Login: per email+host, and per originating IP. WorkOS is the credential oracle, so this
    # is what stands between us and unbounded password spraying.
    rate_limit_login_per_15m: int = 10
    rate_limit_login_ip_per_15m: int = 30
    # MFA code verification: per pending challenge (one person's six digits — a million values,
    # so this is what makes a challenge worth only a handful of guesses) and per originating IP.
    rate_limit_mfa_per_challenge: int = 8
    rate_limit_mfa_ip_per_15m: int = 40
    # Forgot-password: per email (email bombing) — the real control — and a per-portal-host spend
    # ALARM. The host key is shared by every user of that portal and the caller varies the email
    # field of an unauthenticated form to reach it, so it is ADVISORY: exceeding it logs at ERROR,
    # increments graphene_rate_limit_budget_exceeded_total and degrades /health/ready's `abuse`
    # probe, but it never refuses a request. A refusing host cap was a firm-wide password-reset
    # outage for the price of N anonymous POSTs (app/ratelimit.py). Size it for a firm's
    # legitimate peak so the alarm means something; the per-email cap is what bounds the abuse.
    rate_limit_forgot_password_per_hour: int = 5
    rate_limit_forgot_password_host_per_hour: int = 500
    # Email-template test sends (the one AUTHENTICATED capped surface): per acting user — who can
    # only ever mail THEMSELVES, so this bounds our Resend spend and their own inbox, not access —
    # plus a per-firm spend ALARM which is shared by every author at the firm and therefore
    # ADVISORY (refusing on it would stop a colleague's legitimate test). See app/ratelimit.py.
    rate_limit_email_test_per_hour: int = 20
    rate_limit_email_test_tenant_per_hour: int = 200
    # Per-user caps on two AUTHENTICATED per-keystroke reads: Postcoder (a paid upstream — the cap
    # bounds our spend from a stuck key or a scripted session) and search (a multi-table fan-out).
    # Sized well above a person typing; 0 disables.
    rate_limit_postcoder_per_minute: int = 60
    rate_limit_search_per_minute: int = 120

    # ---- Observability (app/logging.py) ----------------------------------------------------
    # 'json' (one object per line — what a log shipper wants) or 'text' (human, for a console).
    # Blank resolves to 'text' when app_stage is 'dev', 'json' otherwise.
    log_format: str = ""
    log_level: str = "INFO"
    # Error tracking. OFF unless a DSN is set AND the optional `sentry-sdk` package is
    # installed; without it, failures still reach the structured log at ERROR (never nowhere).
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0
    # Tail of the trace sampler (app/tracing.py). The OTEL_* variables belong to the SDK and
    # are not settings. Blank (compose's empty default) keeps these defaults. keep_errors off
    # leaves only the ratio: a 500 and a slow request are dropped with the rest.
    portals_otel_keep_errors: bool = True
    portals_otel_slow_request_ms: int = 1000
    # Prometheus text exposition at GET /metrics (queue depth, oldest-queued age, throttle
    # rejections, error counts). Keep the path internal; set a token to require `x-metrics-token`.
    metrics_enabled: bool = True
    metrics_token: str = ""
    # Where the render worker stamps its per-loop heartbeat, read by its own container
    # healthcheck (`python -m app.worker --healthcheck`). Blank => a file in the temp dir.
    worker_heartbeat_path: str = ""
    # Preview rendering runs on the SAME engine as generation — the worker — so a preview is
    # byte-identical to the generated document and the (possibly Windows) API never needs the native
    # render stack. When set, the API's template previews POST the rendered HTML to the worker's
    # synchronous render endpoint at this base URL (e.g. http://render:8001 in compose,
    # http://localhost:8001 in local dev); blank => the API renders locally (needs WeasyPrint).
    render_service_url: str = ""
    # Worker side: the synchronous preview-render HTTP endpoint. `port` binds it; `concurrency` caps
    # simultaneous renders so a burst of previews can't starve the generation loop (previews are
    # second-class to client-facing generation); results are cached by content hash so re-opening a
    # preview (and the editor↔overview double-render) is instant.
    render_service_port: int = 8001
    render_service_concurrency: int = 2

    # WorkOS (identity). Left blank until secrets are provisioned.
    workos_api_key: str = ""
    workos_client_id: str = ""
    # Optional defence-in-depth: pin the access-token issuer. When set, the JWT
    # `iss` claim must match exactly. WorkOS User Management tokens are issued by
    # `https://api.workos.com/user_management/<client_id>` (confirm against a real
    # token — a custom auth domain changes the host). Empty = not enforced.
    workos_issuer: str = ""
    # WorkOS invitations are the onboarding credential. Unlike password-reset tokens (10 minutes),
    # their lifetime is configurable from 1–30 days; seven is WorkOS's default too.
    invitation_expires_days: int = 7

    # Envelope-encryption master key (a Fernet key) for per-tenant WorkOS environment
    # secrets stored in auth.environment_secrets. The API is the sole holder (the portal
    # fetches decrypted config from the internal endpoint). Generate with
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
    # Empty until provisioned — secret handling then fails closed. Never in git.
    secrets_master_key: str = ""

    # Shared secret guarding the internal service-to-service endpoints (e.g. the portal
    # fetching per-host WorkOS config). Empty disables those endpoints (404). Never in git.
    internal_api_token: str = ""

    # Resend (transactional email). We send invites / password-set / verification emails
    # ourselves (self-hosted onboarding) instead of AuthKit's hosted emails. Empty => email
    # sending is disabled (provisioning still works; nothing is sent). Never in git.
    resend_api_key: str = ""
    # Default From for transactional email. A firm with a verified sending domain uses the
    # address operations chose on it (`email.sender_domains`); this is the fallback.
    # Format: "Name <addr@domain>".
    email_from: str = "Graphene <no-reply@grapheneplatforms.com>"

    # Postcoder address autocomplete and UK bank validation (ws.postcoder.com). The portals'
    # /api/postcoder BFF forwards here; the key must never leave this process (the upstream
    # takes it as a query parameter, or in the bank URL path). Blank ⇒ address lookup answers
    # 503 and the field accepts manual entry, and a bank save proceeds without the directory check.
    postcoder_api_key: str = ""

    # THE NON-PRODUCTION SEND GUARD. When `app_stage` is `dev` or `staging`, a recipient is mailed
    # ONLY if it matches an entry here: a full address (`craig@grapheneplatforms.com`) or a domain
    # (`@grapheneplatforms.com`, or the bare `grapheneplatforms.com`). Comma-separated or a JSON
    # array; case- and whitespace-insensitive. Every other stage, including production, consults
    # it not at all.
    #
    # UNSET MEANS `@grapheneplatforms.com`, so dev and staging mail Graphene addresses without a
    # deploy entry. A list saved from the admin tenant page replaces this for the whole environment
    # until it is reset; this value is what "reset" returns to. An EXPLICIT BLANK parses to [] and
    # means send nothing. That fail-safe, and the match rule, live in `app/email/transport.py` (the
    # note above `suppression_reason`); do not restate them here, because a second copy is how the
    # two come apart.
    email_allowlist: Annotated[list[str], NoDecode] = ["@grapheneplatforms.com"]
    # ON BY DEFAULT. When false, the stage/allowlist suppression above does nothing: a send is
    # best-effort, gated only by RESEND_API_KEY being present, in every stage.
    # `EMAIL_NONPROD_GUARD=false` is the break-glass for a dev or staging deployment that must mail
    # real addresses.
    email_nonprod_guard: bool = True
    # Base URL of the internal-operations (admin) portal, for building set-password links for
    # ops staff (whose host isn't in tenant_environments). Tenant portals use their registered
    # client_portal_host / platform_host. e.g. https://ops.grapheneplatforms.com
    operations_portal_url: str = ""
    # Public base URL of the client portal, used to build shareable document links that open the
    # client-portal landing page (e.g. https://portal.example or http://localhost:5173 in dev).
    # Blank falls back to the request host.
    client_portal_base_url: str = ""

    # How long a member of staff may stay in the client portal acting as one of their clients
    # (app/view_as.py). Long enough to work through a telephone call, short enough that a tab
    # left open over lunch is closed by the clock rather than by someone remembering.
    view_as_session_minutes: int = 60
    # The window the single-use handoff ticket has to be exchanged for the session token. It is
    # redeemed by the client portal the instant the new tab opens, so this only has to cover a
    # page load — it is not a period anyone waits through.
    view_as_handoff_seconds: int = 120
    # How long a one-time identity-photo link stays open (app/idv/phone.py). Long enough to
    # find the phone, scan the code and take the photos. The code itself works once, inside
    # this window; opening it is what spends it.
    identity_phone_minutes: int = 30
    # Where the identity-photo link opens, when it is not the client portal's own base URL. Dev
    # only: a phone allows a live camera on a secure page alone, so a local test points this at
    # the https proxy in front of the dev portal (scripts/phone-https-proxy.mjs). Blank uses
    # `client_portal_base_url`, then the firm's registered portal.
    identity_phone_base_url: str = ""

    # Object storage (S3-compatible: AWS S3 / Cloudflare R2 / MinIO / RustFS / Backblaze) —
    # see docs/storage.md. The credential here is the PLATFORM ROOT credential: it provisions
    # the per-tenant buckets (t-{uid}) and performs the API's own writes. `s3_endpoint_url`
    # empty => AWS S3; set it for R2/MinIO/RustFS. Blank endpoint + creds => storage is
    # unconfigured: no buckets are minted and branding falls back to data: URIs.
    # `s3_bucket`/`s3_public_base_url` are the LEGACY shared public branding bucket, used as
    # the fallback while a tenant's own bucket has no public prefix (blank disables it).
    s3_endpoint_url: str = ""
    s3_region: str = "auto"
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_public_base_url: str = ""
    # Core bucket for raw/staged provider data (Graphene-internal; never tenant-reachable).
    # Reserved for the ingestion/ETL plane — the API only provisions/uses tenant buckets.
    # Holds `datalake/{provider}/…` (the provider DuckLake zones) and `files/{provider}/…`
    # (provider-sourced files), i.e. everything that belongs to a PLATFORM we integrate with:
    # Seccl, SEI, Morgan Lloyd.
    s3_core_bucket: str = ""
    # Store kind recorded in the tenant-storage registry: rustfs | s3 | minio. Blank = infer
    # (custom endpoint => rustfs, the local store; no endpoint => plain AWS S3). Set it when
    # deploying against MinIO or another custom-endpoint store.
    s3_kind: str = ""

    # DuckLake catalogue host (docs/datalake.md): the `datalakes` Postgres database, as the
    # lake_writer role — used only to ensure a tenant's catalogue schema at provisioning.
    # Blank => the catalogue step is skipped. Local: the compose init script creates the DB
    # + roles (postgresql+psycopg://lake_writer:lake_writer@localhost:5432/datalakes).
    datalake_database_url: str = ""
    # The same catalogue DB as the READ-ONLY lake_reader role, as a libpq DSN fragment —
    # interpolated into DuckDB's `ATTACH 'ducklake:postgres:<dsn>'` (app/lake). Blank =>
    # lake queries raise LakeUnavailable. Local:
    #   dbname=datalakes host=localhost port=5432 user=lake_reader password=lake_reader
    datalake_catalog_dsn: str = ""
    # The OLTP portals DB as the `app` login, libpq DSN — pre-attached READ_ONLY as `oltp`
    # on every lake connection for lake↔OLTP joins WITH RLS: per-acquire the caller's
    # claims are published on the attached connection (SET ROLE authenticated +
    # session-scoped set_config, docs/datalake.md §2). Blank => no OLTP attach
    # (register() remains available). Local:
    #   dbname=portals host=localhost port=5432 user=app password=app
    datalake_oltp_dsn: str = ""
    # Lake connection pool (app/lake): ONE tenant per pooled DuckDB connection.
    lake_pool_max_connections: int = 8
    lake_pool_max_per_tenant: int = 2
    # Core-catalogue pool (app/lake/core.py): one provider per connection, one thread at a
    # time. Home holds three at once, and the pool refuses a cap below that.
    lake_core_pool_max: int = 4
    # Per-connection DuckDB caps for both pools (app/lake/attach.py::duckdb_config). The worst
    # case is (lake_pool_max_connections + lake_core_pool_max) x lake_memory_limit, so size the
    # container for that; a query that needs more spills to the temp dir instead.
    lake_memory_limit: str = "512MB"
    lake_threads: int = 2
    # How long a tenant's resolved attach config (bucket + creds) is reused before being
    # re-read from the registry (suspension propagates within this window).
    lake_config_cache_seconds: int = 300
    # Base URLs of the provider interlace daemons (`interlace serve`) — the admin
    # Platforms pages read models/plan/runs/events/streams from them and trigger syncs
    # through them. Blank => the page shows that pipeline service as unreachable.
    # Local: Morgan Lloyd http://127.0.0.1:8102, Seccl http://127.0.0.1:8100
    # CTC illustration calculation engine (Bespoke XML v1.6 -> PDF). No credentials: the
    # endpoint is unauthenticated (`nodb=true`) and the request identifies itself through
    # <agent_name> inside the XML. Default is the URL in docs/morgan-lloyd/api-reference.md.
    ctc_illustration_url: str = "https://cam.ctc.uk.com/Pages/Remote.aspx?wci=RunQuote&nodb=true"
    # OFF by default, and deliberately not inferred from the URL having a default: reaching a live
    # third party is an explicit decision per environment. Off => the CTC1 request is still built
    # and validated, and a clearly-marked placeholder illustration is filed instead of a quotation
    # (app/integrations/ctc/placeholder.py). Set CTC_ENABLED=true where the engine is reachable.
    ctc_enabled: bool = False
    # httpx's per-operation budget. An illustration is two sequential requests (the engine call,
    # then fetching the document it names), hence the separate whole-attempt ceiling below.
    # A real SIPP quote against the test engine exceeded 12s on 2 Oct 2026 and was filed as a
    # ReadTimeout placeholder; a trivial quote the same morning answered in about 3s. 45s is
    # room for a projection without waiting on an engine that has stopped answering.
    ctc_timeout: float = 45.0
    # Hard ceiling on the whole attempt. Past this we stop waiting and file a placeholder rather
    # than leave an adviser watching a spinner. It stays above ctc_timeout so a slow quotation
    # still has time to fetch the PDF afterwards.
    ctc_deadline: float = 60.0
    # Name written into <agent_name> (max 130 chars) and shown on the generated illustration.
    ctc_agent_name: str = ""

    # Which SEI environment outbound calls (payment expectations) target. The credentials
    # themselves are SEI_{ENV}_* env vars read by pkgs/sei, not repeated here — this only
    # says which set to use. i10/i31 are test, p04 is production.
    sei_environment: str = "i31"

    # SEI's own rail fee for a withdrawal — what SEI charges to move the money, the same for
    # every firm on SEI (not a per-connection value: `platform.tenant_platforms.config` is
    # where a value would go if it ever needed to vary by firm; this one does not). BACS carries
    # none, which is why it is not a setting. Defaults are today's published rates; ops changes
    # them here if SEI's pricing changes, with no code deploy needed.
    sei_faster_payment_fee: float = 0.50
    sei_swift_fee: float = 15.00

    # ComplyCube (AML screening and identity checks -- app/complycube). One Graphene account
    # serves the portal and the firm API; isolation is `tenant_id` on `compliance.*`. Blank key
    # means the provider does not exist here: `complycube_configured()` is false, the worker's
    # event loop no-ops and the ops routes answer 503. The webhook secret verifies the inbound
    # `ComplyCube-Signature`; blank refuses every delivery. SCREENING_API_KEY and
    # SCREENING_WEBHOOK_SECRET are the names the firm API process read first, and still work.
    complycube_api_key: str = Field(
        default="", validation_alias=AliasChoices("complycube_api_key", "screening_api_key")
    )
    complycube_base_url: str = "https://api.complycube.com/v1"
    # A `live_` key is used outside production only when this says so: live checks are billed and
    # hold real people's data, so a dev or staging box ignores one unless someone chose it here.
    complycube_allow_live_key: bool = False
    complycube_webhook_secret: str = Field(
        default="",
        validation_alias=AliasChoices("complycube_webhook_secret", "screening_webhook_secret"),
    )

    # Resend's webhook signing secret (Svix, `whsec_...`). BLANK DISABLES the delivery
    # webhook outright rather than accepting unsigned callbacks: that route can mark a
    # message bounced, and absence of configuration means "do nothing" here as an explicit blank
    # EMAIL_ALLOWLIST suppresses every dev and staging send. See app/routers/resend_webhook.py.
    resend_webhook_secret: str = ""

    # THE DELIVERY SAFETY NET. Seconds between passes that ask Resend what became of messages still
    # sitting at `sent`/`delayed`; 0 disables it.
    #
    # ON BY DEFAULT, and deliberately not "off wherever the webhook is registered". The webhook is
    # the primary path and it is the one that can silently stop: an endpoint deleted in the Resend
    # dashboard, a rotated signing secret, a deploy that lost the env var, an outage on their side.
    # Every one of those leaves messages sitting at `sent` for ever with nothing to say why — and a
    # net that is off on the deployment that needed it catches nothing.
    #
    # The cost of running both is near zero WHEN THE WEBHOOK IS WORKING, and that is measured
    # rather than assumed: `messages_pending_idx` (0111) is partial on exactly the unresolved set,
    # so a pass is one index scan over a near-empty index — no sort, no provider call, and the same
    # cost whether the table holds a thousand rows or a million.
    #
    # Where it is NOT free is the case the net exists for — but it is far cheaper than it was. A
    # pass used to spend one provider request PER unresolved message; it now spends ONE for the
    # whole of Resend's most recent page (`transport.fetch_recent_events`), and falls back to
    # per-id calls only for rows too old to be on it — and the scan is newest-first so that the
    # rows it looks at are the ones that page can answer for. A recent backlog therefore costs
    # three requests a minute rather than seventy-five; a row stuck at `delayed` long enough to
    # fall off the page still costs its own call when the cursor reaches it, which is the honest
    # price of not starving the tail.
    #
    # TWENTY SECONDS, and the shorter interval is the point rather than a side effect: a polled
    # stamp records when we LEARNED, because Resend publishes no per-event timestamp outside the
    # webhook payload (see `comms/poll.py`). The only way to make that stamp resemble the truth is
    # to ask more often, and one request per pass is what makes that affordable.
    #
    # A deployment sending heavily with a broken webhook should still fix the webhook rather than
    # lean on this: the fallback fan-out is bounded by `poll.BATCH`, so a backlog older than the
    # page window is where the cost comes back.
    comms_poll_seconds: int = 20

    # THE GLOBAL AUTOMATION KILL SWITCH, at the deployment level. Set it false and the worker's
    # automations loop consumes nothing and dispatches nothing, whatever any tenant's settings row
    # says. Separate from `flows.automation_settings.paused` (per tenant, a product control) on
    # purpose: this one needs no database, no permission and no working API — it is the lever you
    # pull from the deploy config at 3am when automations are misbehaving and you cannot reason
    # about which firm's rows are to blame. The cursor keeps ADVANCING while it is off (nothing is
    # dispatched) — a kill switch that quietly queued a night's events into a stampede the moment
    # it was released would be a worse failure than the one it was pulled for.
    automations_enabled: bool = True

    # The in-app NOTIFICATIONS consumer switch, the sibling of `automations_enabled`. False and the
    # worker's notifications loop tails nothing off `audit.actions` and fans nothing out. The cursor
    # keeps advancing while it is off (same reasoning as automations: releasing the switch must not
    # replay a backlog into a notification stampede).
    notifications_enabled: bool = True

    # The NOTIFICATION-EMAIL subscriber switch — the sibling loop that turns the SAME events into
    # emails (docs/notifications.md). On by default like `notifications_enabled`, but it sends
    # nothing until a category's `email` preference is turned on (default off), so out of the box no
    # notification email leaves. This is the estate-wide hard off-switch for that channel; the
    # cursor advances while off, same no-backlog-stampede reasoning as the others.
    email_notifications_enabled: bool = True

    # THE OUTBOUND-WEBHOOK ALLOWLIST. `webhook_out` names an `endpoint_key`, never a URL: a flow
    # author is a firm's operations person, and letting them type a URL an automation then POSTs
    # client data to is an exfiltration channel with a nice builder UI on it (the same reasoning
    # that closed `send_email`'s recipient list to three named destinations). The mapping from key
    # to URL is deployment configuration — an operator's decision, made once, reviewable in the
    # deploy config — so an unknown key is refused at publish and at runtime rather than guessed.
    # JSON object or `key=url` pairs: {"crm_sync": "https://…"} or `crm_sync=https://…,ops=https://…`
    # https only (an http endpoint would put client data on the wire in clear), validated below.
    automation_webhook_endpoints: Annotated[dict[str, str], NoDecode] = {}

    # THE INBOUND-LOOKUP ALLOWLIST — deliberately a SECOND list, not a reuse of the one above.
    # Outbound and inbound carry different risk: an endpoint we are content to notify is not
    # automatically one we trust to SUPPLY the data a client's journey then branches on (and an
    # endpoint whose answers we trust is not automatically one we want client facts posted to).
    # Merging them would mean adding a webhook target silently widened what a `lookup` may believe.
    # Same shape and the same https-only rule as the webhook list; same "an operator configures it,
    # a flow names a key" property. See `app/flows/lookup.py`.
    automation_lookup_endpoints: Annotated[dict[str, str], NoDecode] = {}

    # THE AI GATES (`app/flows/ai.py`: the `classify` and `draft_message` actions).
    #
    # BLANK MEANS THE FEATURE DOES NOT EXIST. Not "degrades", not "logs a warning": publish refuses
    # a flow that names either action, nothing calls out, and no `anthropic` client is ever
    # constructed (the import is deferred for exactly this reason, so a build without the package
    # boots into the same state a build without a key does). This is the first of the two switches
    # — the second is per tenant (`flows.ai_tenants`), because a configured DEPLOYMENT must not
    # mean an enabled FIRM.
    anthropic_api_key: str = ""
    # The model, in configuration rather than in code, so it can move without a deploy of code.
    # Claude Opus 5 is the default: it is the current most-capable Opus, and a classification a
    # branch decides on is not the place to economise. Both actions run with thinking DISABLED at
    # `low` effort — a closed-label choice and a short draft are not reasoning tasks, and the
    # response is schema-constrained, so the two thinking-off failure modes (a tool call written as
    # text, `<thinking>` leaking into output) have nothing to bite on here: no tools are offered and
    # the output is a JSON object validated against a schema.
    ai_model: str = "claude-opus-5"
    # The default per-tenant, per-calendar-month token ceiling stamped on a NEW `flows.ai_tenants`
    # row. It is a default, not the bound: the bound is the row's own column, which an operator can
    # raise or lower per firm. See `app/flows/ai_usage.py` for what happens when it is reached
    # (a refusal on the step, visible and retryable — never a silent stop).
    ai_default_monthly_token_cap: int = 1_000_000

    interlace_morgan_lloyd_url: str = ""
    interlace_seccl_url: str = ""
    # SEI's daemon. Absent until now, which is why SEI had no admin surface at all and why
    # nothing could react to one of its runs — the enablement and Refresh actions both address a
    # daemon by URL, so SEI's sync completing was an event nothing was listening for.
    interlace_sei_url: str = ""
    # Per-tenant bucket name prefix (bucket = prefix + the tenant uid's hex, e.g.
    # tnt-ab12cd34ef567890). AWS names are global, so hosted deployments namespace it
    # (e.g. graphene-prod-tnt-).
    #
    # Changing this does NOT rename anything: `storage.tenants.bucket` is written once and
    # then treated as immutable (the bucket exists remotely, and a DuckLake catalogue's
    # `data_path` metadata names it). Existing tenants move with
    # `scripts/rename_tenant_buckets.py`, which copies the objects and rewrites that one
    # metadata row; until it runs, old and new prefixes coexist happily.
    s3_tenant_bucket_prefix: str = "tnt-"
    # Per-object canned ACL sent on upload. Set `public-read` on providers that use object ACLs for
    # public access (AWS S3, MinIO). Leave blank for providers that reject ACLs and use a bucket
    # policy / public-bucket toggle instead (Cloudflare R2, Backblaze B2).
    s3_acl: str = ""
    # Addressing style: blank => path-style when `s3_endpoint_url` is set (MinIO/R2), virtual-hosted
    # for plain AWS. Set "true"/"false" to force path/virtual if a provider/domain needs the other.
    # (Plain str, not bool, so a blank env value can't fail to parse — interpreted in storage.py.)
    s3_force_path_style: str = ""
    # Private document storage (uploaded + generated docs). A SEPARATE bucket from the public asset
    # bucket above, reusing the same endpoint/credentials/addressing. Private: no public base URL —
    # bytes reach the browser only via a presigned URL or a proxied stream. Blank disables document
    # storage (no data-URI fallback; document routes surface a clear error). Local dev: RustFS.
    s3_documents_bucket: str = ""

    # Background reconcile loop: how often (seconds) to poll WorkOS and re-sync every tenant's
    # members into the mirror. For dev/self-hosted setups with no public webhook endpoint this
    # keeps things in sync continuously. 0 disables it (rely on manual sync / future webhooks).
    # Defaults ON: it used to default to 0, which meant the mirror drifted silently in production
    # (onboarded users rendered as "Suspended" forever) with no webhook consumer to cover it.
    # The worker owns this loop; a transaction-scoped advisory lock per environment also makes
    # overlapping deploy generations and future worker replicas safe.
    workos_sync_poll_seconds: int = 60

    # client :5173, admin :5174, platform :5175 (dev origins for the three portals).
    # `NoDecode` stops the env source from JSON-decoding this field, so the raw
    # string reaches the validator below — which accepts either a JSON array or a
    # plain comma-separated list. Without it, a non-JSON value (the natural thing
    # to type into a deploy UI) raises SettingsError and crashes the app at start.
    allowed_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
    ]

    # Shared, host-keyed portals mean the API is called from many tenant origins. List the
    # static dev origins in `allowed_origins`; set this regex to also allow tenant portal
    # hosts in production (e.g. `^https://[a-z0-9-]+\\.graphene\\.app$` or an alternation of
    # custom domains). Empty = only `allowed_origins` are allowed. Anchored + canary-checked by the
    # validator below: with `allow_credentials=True` a too-broad pattern is a session-theft
    # primitive, so a pattern that matches an arbitrary attacker origin fails startup.
    allowed_origin_regex: str = ""

    @field_validator("allowed_origin_regex", mode="after")
    @classmethod
    def _validate_origin_regex(cls, value: str) -> str:
        """Anchor and sanity-check the operator-supplied CORS origin regex.

        Three things, all because this pattern is paired with ``allow_credentials=True`` — a
        match hands the caller a tenant's authenticated session:

        1. It must compile (a typo'd pattern would otherwise raise deep inside Starlette on the
           first preflight, or — worse — be silently ignored).
        2. It is anchored (``^(?:…)$``). Starlette 1.3 already ``fullmatch``es it, so this is
           belt-and-braces against a future/older middleware that ``match``es or ``search``es.
        3. It is probed with adversarial origins. A pattern that admits an origin we obviously
           don't own (a bare attacker host, an http origin, ``null``, or our own name as a
           *prefix* of someone else's domain) is a misconfiguration we refuse to boot with,
           rather than one that quietly leaks sessions. The canaries are all host-shaped —
           a browser ``Origin`` carries no path or userinfo — so a legitimately broad
           subdomain wildcard still passes.
        """
        pattern = value.strip()
        if not pattern:
            return ""
        anchored = (
            pattern if pattern.startswith("^") and pattern.endswith("$") else f"^(?:{pattern})$"
        )
        try:
            compiled = re.compile(anchored)
        except re.error as exc:
            raise ValueError(f"ALLOWED_ORIGIN_REGEX is not a valid regex: {exc}") from exc
        canaries = (
            "https://evil.example",
            "https://evil.example.com",
            "http://evil.example",
            "https://attacker.test",
            "null",
            "https://graphene.app.evil.example",
        )
        for origin in canaries:
            if compiled.fullmatch(origin):
                raise ValueError(
                    "ALLOWED_ORIGIN_REGEX is too broad — it matches the untrusted origin "
                    f"{origin!r}. Pin the scheme and the host suffix you own, e.g. "
                    r"^https://(client|platform|admin)\.example\.com$"
                )
        return anchored

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _parse_allowed_origins(cls, value: object) -> object:
        """Accept a JSON array (``["https://a","https://b"]``) or a comma-separated
        string (``https://a,https://b``). Non-string values (the default list, or a
        list supplied programmatically) pass through untouched."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            return json.loads(text)
        return [origin.strip() for origin in text.split(",") if origin.strip()]

    @field_validator("email_allowlist", mode="before")
    @classmethod
    def _parse_email_allowlist(cls, value: object) -> object:
        """Accept a JSON array or a comma-separated string (reuses the list parser).

        Entries are NOT normalised here on purpose: ``app/email/transport.py`` lowercases and strips
        at match
        time, so a value monkeypatched in a test or set programmatically is matched by the same
        rules as one that arrived through the env parser. One place decides what matches."""
        return cls._parse_allowed_origins(value)

    @field_validator("automation_webhook_endpoints", "automation_lookup_endpoints", mode="before")
    @classmethod
    def _parse_endpoint_allowlist(cls, value: object, info: ValidationInfo) -> object:
        """Accept a JSON object or ``key=url`` pairs, and refuse anything that is not https.

        Refusing at STARTUP rather than at send time is the point: a mistyped scheme in the deploy
        config would otherwise surface as client data posted in clear to the first automation that
        fires, at whatever hour that happens to be. Both allowlists share this because the rule is
        the same one — they are two lists so that TRUST is separate, not so that parsing is."""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                value = {}
            elif text.startswith("{"):
                value = json.loads(text)
            else:
                value = dict(
                    pair.split("=", 1) for pair in text.split(",") if pair.strip() and "=" in pair
                )
        if not isinstance(value, dict):
            return value
        out: dict[str, str] = {}
        for key, url in value.items():
            endpoint = str(url).strip()
            if not endpoint.startswith("https://"):
                raise ValueError(
                    f"{(info.field_name or '').upper()}['{key}'] must be an https URL — an "
                    "automation puts client data on that wire"
                )
            out[str(key).strip()] = endpoint
        return out

    @field_validator("portals_otel_keep_errors", mode="before")
    @classmethod
    def _blank_otel_keep_errors(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return True
        return value

    @field_validator("portals_otel_slow_request_ms", mode="before")
    @classmethod
    def _blank_otel_slow_request(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return 1000
        return value

    @field_validator("invitation_expires_days")
    @classmethod
    def _valid_invitation_expiry(cls, value: int) -> int:
        if not 1 <= value <= 30:
            raise ValueError("INVITATION_EXPIRES_DAYS must be between 1 and 30")
        return value


settings = Settings()
