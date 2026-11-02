import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from api_log import clear_default_recorder, set_default_recorder
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from intelliflo import client as intelliflo_client

from app import api_log as api_log_module
from app import (
    countries,
    crypto,
    currencies,
    datalake,
    live_push,
    permissions,
    providers,
    tenant_registry,
)
from app.actions.attempt import OperationError
from app.actions.runs import ActionError
from app.api_log import ApiLog
from app.audit_actions import ActionEventsMiddleware, operation_id_for
from app.config import settings
from app.db import system_transaction
from app.lake import core as lake_core
from app.logging import (
    CorrelationIdMiddleware,
    configure_logging,
    init_error_tracking,
    init_tracing,
    shutdown_tracing,
)
from app.notifications import realtime as notifications_realtime
from app.routers import (
    actions,
    admin,
    aml,
    applications,
    automations,
    automations_platform,
    catalog,
    classifications,
    client_groups,
    client_identity,
    client_money,
    content,
    contributions,
    crm,
    datasets,
    documents,
    email_templates,
    exports,
    files,
    firm,
    flow_insights,
    flows,
    forms,
    health,
    identity_phone,
    illustration_reference,
    illustrations,
    integrations,
    internal,
    lookthrough,
    me,
    messaging,
    notifications,
    partner_access,
    platform_catalogue,
    postcoder,
    ref,
    reports,
    resend_webhook,
    reviews,
    search,
    storage,
    tasks,
    templates,
    terms,
    view_as,
)
from app.tracing import instrument_api

logger = logging.getLogger(__name__)


def custom_generate_unique_id(route: APIRoute) -> str:
    """Stable, readable operation ids -> clean method names in the generated TS client.

    Delegates to `audit_actions.operation_id_for` so the OpenAPI spec and the action-event
    vocabulary are the SAME string by construction. They used to agree by coincidence — the
    spec applied this function and the stream read `route.unique_id`, which FastAPI 0.141
    stopped keeping in step (see that function's note; the audit stream's AML exclusion was
    the casualty).
    """
    return operation_id_for(route)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Logging first, before anything else can emit an unformatted line.
    configure_logging("api")
    # Before anything else is waiting: a reload's SIGTERM has to close the open SSE streams
    # while uvicorn is still willing to let them finish. See live_push.arm_server_stop.
    live_push.arm_server_stop()
    init_error_tracking("api")
    # No-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set, so local dev and tests stay quiet.
    init_tracing("portals-api")
    # NOTE: the legacy WorkOS reconcile (sync.reconcile_on_startup) is DISABLED. It is an
    # org-per-tenant sync that pulls every WorkOS *organisation* into auth.tenants — under
    # project-per-tenant (docs/identity-and-access.md §0), orgs are PORTALS, not tenants, so
    # running it corrupts the tenant list on every boot. A project-per-tenant, per-project
    # sync will replace it (project-context §12.4). Do not re-enable as-is.
    # Load the per-tenant environment registry so the shared API can verify tokens from
    # every tenant's WorkOS environment (empty => single-environment fallback).
    await tenant_registry.load_on_startup()
    # Sync the canonical permission registry (app/permissions.py) into auth.permissions — the
    # single source of truth, idempotent, so new permissions land without a hand-written migration.
    async with system_transaction() as session:
        await permissions.sync_permissions(session)
        # ref.currencies and ref.countries are seed data. Load each once; the process
        # does not re-read them.
        await currencies.load(session)
        await countries.load(session)
    # `sync_platforms` used to run here, upserting one `platform.platforms` row per provider so
    # the eight `platform_id` foreign keys had something to point at. 0131 replaced those keys
    # with `platform.provider_code`, so the registry table is gone and there is nothing to seed:
    # the vocabulary is `providers.PROVIDERS` and the enum, and a provider that exists in code
    # exists in the database by construction rather than by a startup write that could not have
    # run yet.
    # Let lake_reader into the provider catalogue schemas. The interlace daemons create
    # them as lake_writer and nothing granted USAGE, so every read-only attach on a
    # provider catalogue (Platforms warehouse counts, SQL console, enablement options)
    # failed with "permission denied for schema". Idempotent; failure is not fatal —
    # the datalakes DB is optional and the reading endpoints degrade on their own.
    try:
        await datalake.ensure_provider_catalogs(
            {providers.provider_schema(p) for p in providers.PROVIDERS}
        )
    except Exception:
        logger.warning("could not grant lake_reader on the provider catalogues", exc_info=True)
    # Pre-open one core-catalogue connection per provider, so the first clients or accounts
    # listing after a deploy does not pay the extension downloads and the attach itself
    # (`lake_core.warm`). In the background: a request that lands first opens its own connection
    # exactly as before, and an unreachable warehouse delays nothing but its own figures.
    warm_task = asyncio.create_task(asyncio.to_thread(lake_core.warm, lake_core.warm_order()))
    # Background WorkOS reconcile loop (dev/self-hosted, no webhooks). No-op when
    # WORKOS_SYNC_POLL_SECONDS is 0.
    # Drain for the outgoing-call audit. Started here so a call recorded during startup (a
    # provider token refresh) is not queued with nothing to write it.
    _app.state.api_log = ApiLog()
    await _app.state.api_log.start()
    # Also on the module-level slot, which is how a call site that needs a recorder CARRYING the
    # acting tenant and user reaches it. `platform_actions.run` used to do `from app.main import
    # app` inside the function to read `app.state` — a service locator that makes the importer
    # construct the whole application, and that silently returns None in any process which is not
    # this one.
    api_log_module.install(_app.state.api_log)
    # Installed process-wide, not merely held on app.state: every vendor client falls back to
    # this when it is handed no recorder of its own, which is what makes the log record at all.
    # Before this, all four clients accepted a recorder and not one call site supplied one, so
    # audit.api_log had a table, a drain, endpoints and an admin page and stayed permanently
    # empty. A call site that knows the acting tenant and user still builds its own recorder;
    # this one is the floor under the ones that do not.
    set_default_recorder(_app.state.api_log.recorder(source="app"))
    # The realtime notification bridge: LISTENs for deliveries the WORKER announces and pushes them
    # to the SSE subscribers this API process holds (the cross-process fan-out messaging didn't
    # need). Only when notifications are on — otherwise nothing is ever announced, so the held
    # connection would idle for no one.
    realtime_task = (
        asyncio.create_task(notifications_realtime.listen_forever())
        if settings.notifications_enabled
        else None
    )
    try:
        yield
    finally:
        # Stop the audit drain before its database resources disappear; queued calls are flushed.
        clear_default_recorder()
        api_log_module.install(None)
        await _app.state.api_log.stop()
        if realtime_task is not None:
            realtime_task.cancel()
        # The warm-up thread cannot be interrupted mid-attach; cancelling only stops waiting
        # for it. The pooled connections it opened close with the pool.
        warm_task.cancel()
        lake_core.reset_pool()
        await intelliflo_client.aclose_shared_client()
        shutdown_tracing()


app = FastAPI(
    title="Graphene Platform API",
    version="0.0.0",
    generate_unique_id_function=custom_generate_unique_id,
    lifespan=lifespan,
)

# Added FIRST on purpose: add_middleware prepends, so the first one added sits INNERMOST — the
# action-event middleware must run inside CorrelationIdMiddleware (its events carry the request's
# correlation id via the contextvar) and needs the routed scope + the principal the auth
# dependency stashes, both of which only exist inside the router. See app/audit_actions.py.
app.add_middleware(ActionEventsMiddleware)

# Added BEFORE CORS on purpose: add_middleware prepends, so the last one added sits outermost.
# CORS must stay outermost to short-circuit preflights without running the rest of the stack.
app.add_middleware(CorrelationIdMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    # Host-keyed shared portals: tenant origins are matched by regex in production
    # (the static list stays for local dev). Empty regex => only the static list.
    # settings validates/anchors the pattern — an unanchored regex plus allow_credentials
    # would be a cross-origin session-theft primitive.
    allow_origin_regex=settings.allowed_origin_regex or None,
    allow_credentials=True,
    # Narrowed from "*": with credentials, only the verbs and headers we actually use.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["authorization", "content-type", "x-correlation-id"],
)


@app.exception_handler(crypto.SecretsNotConfigured)
async def secrets_not_configured_handler(
    request: Request, exc: crypto.SecretsNotConfigured
) -> JSONResponse:
    """An instance misconfiguration (SECRETS_MASTER_KEY unset/invalid), not a bug — surface
    it as 503 with the actionable message instead of a bare 500 traceback. Raised wherever
    secret handling fails closed (environment secrets, storage credentials, …)."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ActionError)
async def action_error_handler(request: Request, exc: ActionError) -> JSONResponse:
    """A door's domain refusal (switched off, not allowed, already open, needs a reason). Mapped
    here as well as in each door's own try/except, so a door that forgets to catch one answers with
    its status, never a bare 500."""
    return JSONResponse(status_code=getattr(exc, "status", 422), content={"detail": str(exc)})


@app.exception_handler(OperationError)
async def operation_error_handler(request: Request, exc: OperationError) -> JSONResponse:
    """A vendor refusal or outage that reached the HTTP layer uncaught: a permanent one is a 422
    (the request itself is wrong), otherwise a 502 (the provider could not be reached). Nothing was
    sent. A door that catches this itself keeps its own handling (it records the booking and returns
    202); this is the safety net for the paths that do not."""
    permanent = getattr(exc, "permanent", False)
    return JSONResponse(
        status_code=422 if permanent else 502,
        content={"detail": str(exc) or "The provider could not complete this request."},
    )


app.include_router(health.router)
app.include_router(me.router)
app.include_router(admin.router)
app.include_router(partner_access.router)
app.include_router(firm.router)
app.include_router(crm.router)
app.include_router(classifications.router)
app.include_router(client_groups.router)
app.include_router(client_money.router)
app.include_router(lookthrough.router)
app.include_router(integrations.router)
# Contributions and transfers share a module (one ledger, two domains) but mount separately.
app.include_router(contributions.router)
app.include_router(contributions.transfers_router)
app.include_router(search.router)
app.include_router(postcoder.router)
# Deployment capabilities (is digital IDV on), not tenant data — see app/routers/ref.py.
app.include_router(ref.router)
app.include_router(reports.platform)
app.include_router(reports.admin)
app.include_router(platform_catalogue.router)
app.include_router(illustrations.router)
app.include_router(illustration_reference.router)
app.include_router(catalog.router)
app.include_router(documents.router)
app.include_router(files.router)
app.include_router(resend_webhook.router)
app.include_router(flows.router)
# The two read-only analysis surfaces over a flow: the pre-publish dry run and the step funnel.
# Its own module, not a section of `flows`, because it shares no state with the authoring CRUD and
# runs on a book session (so the admin portal mounts both read-only via `?tenant=`).
app.include_router(flow_insights.router)
# The rule tier's authoring surface. Separate from `flows` deliberately — separate permissions
# (automations:* vs flows:*), separate portal section, and a firm-scoped session rather than the
# flow router's tenant_session.
app.include_router(automations.router)
# The estate-wide kill switch, on the operations surface. Its own module for the reason the two
# surfaces do not overlap: firm-scoped authoring vs an operations-only, tenant-less control.
app.include_router(automations_platform.router)
app.include_router(applications.router)
app.include_router(tasks.router)
app.include_router(actions.router)
app.include_router(reviews.router)
app.include_router(notifications.router)
app.include_router(forms.router)
app.include_router(templates.router)
app.include_router(email_templates.router)
app.include_router(content.router)
app.include_router(content.uploads)
app.include_router(content.crm_router)
app.include_router(terms.router)
app.include_router(terms.uploads)
app.include_router(terms.contacts_router)
app.include_router(datasets.router)
app.include_router(exports.router)
app.include_router(storage.router)
app.include_router(aml.router)
app.include_router(client_identity.router)
app.include_router(internal.router)
app.include_router(messaging.router)
app.include_router(view_as.router)
app.include_router(identity_phone.router)

# After the middleware stack. add_middleware prepends, so this server span wraps the
# request, including the CORS preflight short-circuit. http.route is the route template.
instrument_api(app)
