"""DocumentWorker — drains ``documents.render_jobs`` and renders generated documents (§10).

Lifecycle per job: claim (``FOR UPDATE SKIP LOCKED``) → load the queued document + its published
template version → render the version's HTML (Jinja2 → WeasyPrint/Chromium) → store the PDF → mark
the document ``generated`` + write the ``document.generated`` audit event → ack the job. On error it
retries with backoff up to ``max_attempts``, then dead-letters (job ``failed`` + document
``failed`` with the error). Runs as ``service_role`` (BYPASSRLS), stamping the job's ``tenant_id``
on every write — the 0001 system pattern; the worker has no HTTP principal.

Deps (WeasyPrint's native stack) mean this runs in the Linux worker container (compose ``render``
service); the Windows-native API just enqueues. Run: ``python -m app.worker``.

This process hosts independent loops — among them the render drain, the flow outbox dispatcher, the
task notifier, the ComplyCube event consumer, the firm webhook sender, the Flows v3 automation
consumer (which tails ``audit.actions`` by cursor and fires armed automations) and the Flows v3
timer loop (which wakes cases parked on a ``wait`` / ``wait_for_event`` node) — as separate asyncio
tasks, so a multi-second WeasyPrint render does not hold up flow dispatch behind it. Each loop
stamps a heartbeat that the container's own healthcheck reads (``python -m app.worker
--healthcheck``): without it a crash-looping or wedged worker looks like a happily running
container, and documents/notifications just silently stop.

**It also closes the flow engine's loop.** A ``generate_document`` step enqueues a render job and
reports WAITING; the ONLY thing that can move it on is this worker calling
``orchestrator.on_document_rendered`` — on success from ``_persist``, and on a dead letter from
``_fail_or_retry``. Without those two calls a journey containing a generated document waits forever
(the exact symptom the functional audit found), so ``tests/test_worker_render_callback.py`` drives
the whole path: step → job → render → callback → step advances.

**A dataset that didn't resolve is never silent.** Rendering goes through
``resolve_datasets_reporting``, and a *blocking* problem (missing/disabled/unpublished dataset,
rejected SQL, a client-scoped query with no client) FAILS the render instead of shipping a
plausible-looking client PDF with empty tables. Advisory problems (an empty or truncated result) are
recorded on the document and in the audit detail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

import uvicorn
from api_log import set_default_recorder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import api_log, poller, render_service, storage, tenant_data, tenant_storage
from app.audit_actions import DOCUMENT_RENDER_EVENT, WORKER_RENDER_SUB, record_document_action
from app.comms import poll as comms_poll
from app.complycube import consumer as complycube_consumer
from app.complycube import deliveries as firm_deliveries
from app.complycube import poller as complycube_poller
from app.complycube.client import complycube_configured, require_client
from app.config import settings
from app.contacts import phone_expr
from app.flows import automation, dispatcher, orchestrator, platform_pause, timers

# `effects` is imported for its SIDE EFFECT: it registers the "action" dispatch target with
# `triggers.TARGETS` at import, exactly as `automation` does for "flow". Without it the automation
# consumer finds no target for every rule-tier automation a firm has armed and records them all as
# `failed` — silently, from the firm's point of view. Do not "remove the unused import".
from app.flows import effects as _effects  # noqa: F401
from app.idv import held as idv_held
from app.logging import (
    configure_logging,
    init_error_tracking,
    init_tracing,
    report_error,
    set_correlation_id,
    shutdown_tracing,
)
from app.notifications import consumer as notifications_consumer
from app.notifications import email_consumer as notifications_email_consumer
from app.render import jinja_engine
from app.render.components import dataset_codes, loop_sources, substitute_components
from app.render.renderers import get_renderer, weasyprint_available
from app.render.resolve import DatasetProblem, first_rows, resolve_datasets_reporting
from app.render.sample import BUILTIN_COLLECTIONS, sample_portfolio
from app.tasks import notifier
from app.tracing import instrument_engine, job_span, traced_job

logger = logging.getLogger("document_worker")

SERVICE_NAME = "render-worker"

POLL_INTERVAL_SECONDS = 2.0
RETRY_BACKOFF_SECONDS = 30
# A job stuck 'processing' longer than this (worker crash mid-render) is reclaimable.
VISIBILITY_TIMEOUT_MINUTES = 5

# The loops, and how long each may go without a heartbeat before the container is unhealthy.
# Generous for `render`: one iteration legitimately spans a slow PDF render, and a healthcheck that
# restarts the worker mid-render would be worse than the problem it is watching for.
RENDER_LOOP = "render"
OUTBOX_LOOP = "outbox"
NOTIFIER_LOOP = "notifier"
COMPLYCUBE_LOOP = "complycube_events"
FIRM_WEBHOOKS_LOOP = "firm_webhooks"
AUTOMATIONS_LOOP = "automations"
TIMERS_LOOP = "timers"
NOTIFICATIONS_LOOP = "notifications"
EMAIL_NOTIFICATIONS_LOOP = "email_notifications"
COMMS_POLL_LOOP = "comms_poll"
TENANT_DATA_LOOP = "tenant_data"
WORKOS_SYNC_LOOP = "workos_sync"
SEI_RECEIPTS_LOOP = "sei_receipts"
BATON_LOOP = "baton"
#: Loops the healthcheck watches, with how long a pass may take before the container is unhealthy.
#: A loop absent from here is NOT watched — which is deliberate for the ones that can be switched
#: off, since "never ran" and "wedged" are the same reading to `healthcheck` and an off-by-config
#: loop would report unhealthy for ever.
LOOP_STALL_SECONDS: dict[str, float] = {
    RENDER_LOOP: 900,
    OUTBOX_LOOP: 120,
    NOTIFIER_LOOP: notifier.SCAN_INTERVAL_SECONDS * 3,
    # One pass is one batch of 20 inbox rows, each a handful of provider GETs with their own
    # retries. It beats when ComplyCube is not configured too, so it is always watched.
    COMPLYCUBE_LOOP: 600,
    # One pass is one batch of firm webhook sends, all at once, each capped at 10 seconds, between
    # two short transactions. It beats when signing is not configured too, so it is always watched.
    FIRM_WEBHOOKS_LOOP: 300,
    # One iteration is a bounded batch of stream rows; the slowest thing in it is a
    # start_execution. Tight enough that a wedged consumer — automations silently not firing —
    # shows up as an unhealthy container rather than as a firm's chasers quietly never happening.
    AUTOMATIONS_LOOP: 300,
    # One iteration is a bounded batch of due timers, and the slowest thing in it is one case
    # resumption. Same allowance as the consumer, and for the same reason: a wedged timer loop is
    # invisible from the outside — every parked journey simply never wakes up — so it has to show
    # as an unhealthy container rather than as a client who was never chased.
    TIMERS_LOOP: 300,
    # One iteration is a bounded batch of stream rows fanned out to notification rows — a handful of
    # cheap resolver queries + inserts per action. Same allowance and reason as the automation
    # consumer it twins: a wedged tail is invisible (notifications simply stop) and must surface as
    # an unhealthy container.
    NOTIFICATIONS_LOOP: 300,
    # The notification-email subscriber, the in-app tail's twin. Same bounded-batch reasoning; a
    # wedged email tail means notification emails silently stop, so it must surface as unhealthy.
    EMAIL_NOTIFICATIONS_LOOP: 300,
    TENANT_DATA_LOOP: 1800,
    # One pass re-reads open SEI activities. The idle sleep is fifteen minutes, so the
    # stall window has to be longer than that or a quiet book looks unhealthy.
    SEI_RECEIPTS_LOOP: 1800,
    # One pass claims a single baton run and advances it for a budget. A wedged loop
    # means baton runs stop draining, so it has to surface as unhealthy.
    BATON_LOOP: 900,
}

# The automations loop's cadence. The stream tail polls quickly (an automation that fires a
# minute after the client uploaded their form feels broken); the schedule pass only needs to be
# finer-grained than a schedule slot, and its own grace window absorbs a late run.
AUTOMATIONS_POLL_SECONDS = 3.0
AUTOMATIONS_SCHEDULE_SECONDS = 60.0
# The timer loop's idle cadence. Waits are authored in hours and days, so second-level precision
# buys nothing — but the poll is cheap (one partial-index probe against `timers_due_idx`) and a
# short interval keeps a `duration_hours: 0.01` test-drive from feeling broken. The loop drains
# back-to-back while there is work, so this only bounds the idle case.
TIMERS_POLL_SECONDS = 5.0
# The notifications tail's cadence. Polls quickly like the automation tail — a notification that
# lands minutes after the action that caused it reads as broken. The loop drains back-to-back while
# there is work, so this only bounds the idle case.
NOTIFICATIONS_POLL_SECONDS = 3.0
#: The baton runnable-run queue. One pass claims and advances one run; it drains while there is
#: work, so this only bounds the idle case.
BATON_POLL_SECONDS = 3.0
#: How often the notifications tail runs its REPAIR pass (consumer.resweep_once). Slow on
#: purpose: it exists to catch the rare row the LAG_SECONDS window skipped, not to deliver
#: anything on time, and every pass it makes is a range read that normally writes nothing.
NOTIFICATIONS_RESWEEP_SECONDS = 60.0
# The email tail's idle cadence — same as the in-app tail; both drain back-to-back while busy.
EMAIL_NOTIFICATIONS_POLL_SECONDS = 3.0
# How often the notifier loop repeats its "email is not configured" warning, so the state is
# visible in the log of a long-running container, not just at boot.
DISABLED_NOTICE_SECONDS = 3600.0

# The ComplyCube inbox's idle cadence. A full batch drains again straight away.
COMPLYCUBE_POLL_SECONDS = 5.0
# How often the same pass also asks ComplyCube what has changed (app/complycube/poller.py).
# Polling is how results arrive while ComplyCube's webhooks are off.
COMPLYCUBE_CHECK_POLL_SECONDS = 20.0
# The firm webhook outbox's idle cadence. A full batch sends again straight away.
FIRM_WEBHOOKS_POLL_SECONDS = 5.0


class RenderImpossible(RuntimeError):
    """The render cannot succeed, now or on any retry — so it is not retried.

    A template version with no content will still have none in thirty seconds, and a document whose
    template version has gone will not get one back. Retrying either only holds the document at
    ``queued`` through the backoff, so the operator waits minutes to be told something that was
    already certain on the first attempt.

    Deliberately narrow: the default for an unrecognised failure stays RETRY, because the costly
    mistake is the other way round. A transient blip refused on its first attempt is a document
    that never renders, where a permanent failure retried is only slow.
    """


class DatasetUnresolved(RuntimeError):
    """A dataset the document is built on could not be trusted — fail the render, don't ship it.

    The message is the author-facing ``detail`` of every blocking problem, so it lands on the
    document, in the dead-letter surface and (for a flow-generated document) on the step that
    failed — instead of a client PDF with quietly empty tables.
    """

    def __init__(self, problems: list[DatasetProblem]) -> None:
        self.problems = problems
        super().__init__("; ".join(p.detail for p in problems) or "a dataset did not resolve")


def _warning_note(problems: list[DatasetProblem]) -> str | None:
    """Advisory dataset problems as one line for ``documents.error`` (document still rendered)."""
    advisory = [p for p in problems if p.reason in ("empty", "truncated")]
    if not advisory:
        return None
    return "; ".join(p.detail for p in advisory)[:1000]


async def _claim(session: AsyncSession) -> dict[str, Any] | None:
    """Atomically claim the next ready job (or reclaim one whose visibility timed out)."""
    row = (
        (
            await session.execute(
                text(
                    "UPDATE documents.render_jobs SET status='processing', claimed_at=now(), "
                    "  attempts = attempts + 1, updated_at=now() "
                    "WHERE id = ("
                    "  SELECT id FROM documents.render_jobs "
                    "  WHERE (status='queued' AND run_after <= now()) "
                    "     OR (status='processing' "
                    "         AND claimed_at < now() - make_interval(mins => :vis)) "
                    "  ORDER BY run_after, id FOR UPDATE SKIP LOCKED LIMIT 1"
                    ") "
                    "RETURNING id, tenant_id, document_id, attempts, max_attempts"
                ),
                {"vis": VISIBILITY_TIMEOUT_MINUTES},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


def _format_address(addr: Any) -> str:
    """Flatten a jsonb contact address to a one-line display string — matching the sample-preview
    contract, where `{{ client.address }}` is a plain string. Empty parts are dropped."""
    if isinstance(addr, dict):
        parts = [addr.get(k) for k in ("line1", "line2", "city", "county", "postcode", "country")]
        return ", ".join(str(p).strip() for p in parts if p and str(p).strip())
    return str(addr).strip() if addr else ""


def _format_dob(dob: Any) -> str:
    """Client DOB in the sample-preview display format (`14 May 1980`), or "" when unset — Jinja
    renders `None` as the literal "None", so an absent field must be an empty string, not None."""
    if dob is None:
        return ""
    try:
        return str(dob.strftime("%d %B %Y"))
    except AttributeError:
        return str(dob)


async def _client_context(session: AsyncSession, document_id: str) -> dict[str, Any]:
    """Resolve the client to render for: the actual recipient once sent (document_contacts
    'recipient'), else the client generation was targeted at (intended_contact_id) -- rendering
    needs to know who the document is FOR regardless of whether it's been delivered yet; that's a
    separate concern from documents_read RLS, which only trusts document_contacts for visibility.

    The `client` dict mirrors the sample-preview shape (`render/sample.py::_SAMPLE_CLIENT`) —
    name/first_name/surname/title/email/phone/telephone/address/date_of_birth — so a token the
    editor lint and the generate gate advertise as resolvable (both keyed off `sample_context`) gets
    a real value at render instead of blanking. `id` is kept for subject-scoped dataset resolution.

    **`{{ client.phone }}` stays the mobile.** Renaming the token to `client.mobile` would blank
    the number in every firm-authored template already holding `{{ client.phone }}`. The token
    means "the number to ring this client on". `{{ client.telephone }}` is the other number."""
    row = (
        (
            await session.execute(
                text(
                    # noqa on the next line: `phone_expr` returns a literal built in
                    # app.contacts from a column name of ours -- no request value reaches it.
                    "SELECT c.id, c.name, c.email, c.first_name, c.surname, "  # noqa: S608
                    "       c.title, " + phone_expr("c.mobile") + " AS phone, "
                    "       " + phone_expr("c.phone") + " AS telephone, c.dob, c.address "
                    "FROM documents.documents d "
                    "JOIN crm.contacts c ON c.id = COALESCE("
                    "  (SELECT dc.contact_id FROM documents.document_contacts dc "
                    "     WHERE dc.document_id = d.id AND dc.role = 'recipient' "
                    "     ORDER BY dc.added_at LIMIT 1), "
                    "  d.intended_contact_id"
                    ") "
                    "WHERE d.id = :id"
                ),
                {"id": document_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return {}
    # Coerce nullable fields to "" — a null value would otherwise render as the literal "None".
    client = {
        "id": row["id"],
        "name": row["name"] or "",
        "first_name": row["first_name"] or "",
        "surname": row["surname"] or "",
        "title": row["title"] or "",
        "email": row["email"] or "",
        "phone": row["phone"] or "",
        "telephone": row["telephone"] or "",
        "other_numbers": row["telephone"] or "",
        "address": _format_address(row["address"]),
        "date_of_birth": _format_dob(row["dob"]),
    }
    return {
        "client": client,
        "client_name": row["name"] or "",
        "client_email": row["email"] or "",
    }


# The valuation date of an account's `value`, as `routers/crm.py::_VALUED_AT` computes it. Written
# out rather than imported: the worker must not import a router (that pulls FastAPI's dependency
# graph into a process that serves no requests), and there is no shared accounts module to put it in
# yet. Change the two together — a divergence would date one figure two ways.
_VALUED_AT = (
    "COALESCE((a.data->>'valued_at')::timestamptz, "
    "         (SELECT max(pv.as_of_date)::timestamptz FROM platform.valuations pv "
    "           WHERE pv.account_id = a.id), "
    "         a.updated_at)"
)


async def _client_accounts_context(session: AsyncSession, contact_id: Any) -> dict[str, Any]:
    """The client's own book: `accounts` to loop over, and a `portfolio` roll-up for prose.

    Mirrors `render/sample.py::_SAMPLE_ACCOUNTS` key for key — that module IS the variable
    catalogue the lint reads (`routers/templates.py::_known_variables`), so a key here that is
    missing there is reported to the author as unsourced, and a key there that is missing here
    renders blank in a real document while previewing perfectly. The roll-up itself is
    `sample_portfolio`, imported rather than re-summed for the same reason.

    DRAFT ACCOUNTS ARE EXCLUDED. A draft is an account being set up, not one the client holds, and
    the default has to be the safe one: this data goes into client-facing PDFs, so an unopened
    account appearing in a valuation table is a misstatement we would be sending out. `closed` IS
    included, because an annual review legitimately reports on an account that closed during the
    year — and `status` is exposed so an author who wants only the live ones can say so
    (`{% for a in accounts if a.status == 'active' %}`).

    Values are RAW (numbers, ISO dates) for the filters to format — see `_SAMPLE_ACCOUNTS`.

    Ordered by value, largest first: a valuation table reads that way, and `ORDER BY a.id` would
    order it by whenever each account happened to be created.
    """
    if contact_id is None:
        return {"accounts": [], "portfolio": sample_portfolio([])}
    rows = (
        (
            await session.execute(
                text(
                    # noqa on the next line: every interpolation is a literal defined in this
                    # module — no request value reaches it.
                    "SELECT a.name, a.platform_identifier, a.status::text AS status, "  # noqa: S608
                    "       a.value::float8 AS value, a.currency, a.risk_level, "
                    "       a.opened_at, " + _VALUED_AT + " AS valued_at, "
                    "       p.name AS product_name, p.product_type::text AS product_type, "
                    "       m.name AS model_name, fp.name AS fee_package_name "
                    "FROM platform.accounts a "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "LEFT JOIN platform.models m ON m.id = a.model_id "
                    "LEFT JOIN platform.fee_packages fp ON fp.id = a.fee_package_id "
                    "WHERE a.contact_id = :cid AND a.status <> 'draft' "
                    "ORDER BY a.value DESC NULLS LAST, a.id"
                ),
                {"cid": contact_id},
            )
        )
        .mappings()
        .all()
    )
    accounts = [
        {
            # Coerced to "" for the reason `_client_context` gives: a null would otherwise render as
            # the literal "None" in a client's document.
            "name": row["name"] or "",
            "number": row["platform_identifier"] or "",
            "product": row["product_name"] or "",
            "product_type": row["product_type"] or "",
            "status": row["status"] or "",
            # Money stays None when there is no valuation: 0.00 in a client's valuation table is a
            # statement that the account is empty, which is not the same as not knowing.
            "value": row["value"],
            "currency": row["currency"] or "",
            "risk_level": row["risk_level"] or "",
            "model": row["model_name"] or "",
            "fee_package": row["fee_package_name"] or "",
            "opened": _iso_day(row["opened_at"]),
            "value_as_of": _iso_day(row["valued_at"]),
        }
        for row in rows
    ]
    return {"accounts": accounts, "portfolio": sample_portfolio(accounts)}


def _iso_day(ts: Any) -> str:
    """A timestamp as an ISO date, or "" — the shape the accounts context and the `date` filter
    agree on. Absent stays absent: a document must not claim a date nothing recorded."""
    if isinstance(ts, datetime):
        return ts.date().isoformat()
    if isinstance(ts, date):
        return ts.isoformat()
    return ""


async def _actor_firm_context(
    session: AsyncSession, generated_by: Any, tenant_id: Any
) -> dict[str, Any]:
    """Adviser (who generated the document) + firm (the tenant) merge fields. `sample_context` and
    the generate gate both advertise `adviser`/`adviser_name`/`firm`/`firm_name`, but the worker
    never produced them — so those tokens passed the gate and rendered blank. The worker runs as
    service_role, so these read directly (no RLS filter)."""
    ctx: dict[str, Any] = {}
    if generated_by is not None:
        adviser = (
            (
                await session.execute(
                    text("SELECT first_name, last_name, email FROM auth.users WHERE id = :id"),
                    {"id": generated_by},
                )
            )
            .mappings()
            .one_or_none()
        )
        if adviser is not None:
            name = (
                " ".join(p for p in (adviser["first_name"], adviser["last_name"]) if p)
                or adviser["email"]
            )
            ctx["adviser"] = {"name": name, "email": adviser["email"]}
            ctx["adviser_name"] = name
    firm = (
        (
            await session.execute(
                text("SELECT name, frn, chn FROM auth.tenants WHERE id = :id"),
                {"id": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if firm is not None:
        # frn = FCA Firm Reference Number, chn = Companies House number — stored on auth.tenants but
        # previously never surfaced; a letterhead's regulatory line ({{ firm.frn }}) needs them.
        ctx["firm"] = {
            "name": firm["name"],
            "frn": str(firm["frn"]) if firm["frn"] is not None else "",
            "chn": firm["chn"] or "",
        }
        ctx["firm_name"] = firm["name"]
    return ctx


async def _load_inputs(session: AsyncSession, job: dict[str, Any]) -> dict[str, Any]:
    """Read the queued document + its published template version + bound client (short read txn).

    A GIA or ISA illustration filed before the firm had a document template still prints the
    layout in the repo. Once the template is published, the document points at that version
    and the join below prints it, the same as every other generated document.
    """
    illustration = (
        (
            await session.execute(
                text(
                    "SELECT uid, inputs, metadata, name, generated_by, intended_contact_id, "
                    "       template_version_id "
                    "FROM documents.documents WHERE id = :id"
                ),
                {"id": job["document_id"]},
            )
        )
        .mappings()
        .one_or_none()
    )
    if illustration is None:
        raise RenderImpossible("document or its template version is missing")
    meta = illustration["metadata"] or {}
    if (
        isinstance(meta, dict)
        and meta.get("illustration_engine") == "graphene"
        and illustration["template_version_id"] is None
    ):
        from app.render.illustration_template import TEMPLATE_CSS, TEMPLATE_HTML

        return {
            "doc": {
                "uid": illustration["uid"],
                "inputs": illustration["inputs"] or {},
                "metadata": meta,
                "name": illustration["name"],
                "generated_by": illustration["generated_by"],
                "intended_contact_id": illustration["intended_contact_id"],
                "schema": {"html": TEMPLATE_HTML, "css": TEMPLATE_CSS},
                "version_number": 1,
                "renderer": "weasy",
                "linked_datasets": [],
            },
            "context": {},
            "datasets": {},
            "warnings": [],
        }
    doc = (
        (
            await session.execute(
                text(
                    "SELECT d.uid, d.inputs, d.metadata, d.name, d.generated_by, "
                    "       d.intended_contact_id, "
                    "       v.schema, v.version_number, v.renderer::text AS renderer, "
                    "       t.linked_datasets "
                    "FROM documents.documents d "
                    "JOIN documents.template_versions v ON v.id = d.template_version_id "
                    "JOIN documents.templates t ON t.id = v.template_id "
                    "WHERE d.id = :id"
                ),
                {"id": job["document_id"]},
            )
        )
        .mappings()
        .one_or_none()
    )
    if doc is None:
        raise RenderImpossible("document or its template version is missing")
    schema = doc["schema"] or {}
    if not (isinstance(schema, dict) and schema.get("html")):
        raise RenderImpossible("template version has no rendered content")
    context = await _client_context(session, job["document_id"])
    # Enrich with adviser + firm merge fields (the gate/preview advertise them; without this they
    # render blank). Merges into the context dict the template reads via `{{ adviser_name }}` etc.
    context.update(await _actor_firm_context(session, doc["generated_by"], job["tenant_id"]))
    # The client's accounts, keyed off the client `_client_context` just resolved — so a document
    # sent to a recipient reports the RECIPIENT's book, not the client it was originally aimed at.
    context.update(await _client_accounts_context(session, (context.get("client") or {}).get("id")))
    # Resolve the datasets this document draws on (template links + any component bindings), keyed
    # by code, for the charts/tables/Jinja-loops. Subject-scoped SQL binds to the recipient client.
    subject_id = (context.get("client") or {}).get("id")
    # Codes from three places: the template's declared links, chart/table component bindings, and
    # bare `{% for x in <code> %}` loop sources (Jinja tables/repeat blocks carry no component, so
    # without this their data-source is never resolved and the loop renders empty).
    # A declared link or a component binding is a CONTRACT the author made: if it doesn't resolve,
    # the document would ship with silently empty tables, so the render fails.
    #
    # EXCEPT a built-in collection. `accounts` is offered in the same source pickers as a dataset
    # (`editor/variables.ts::collectionOptions`) and is bound the same way, but the catalogue holds
    # no row for it — so resolving it there answers "no dataset with code 'accounts' in this firm"
    # and, being a contract, kills the render. It is supplied from the context below instead, which
    # is also what makes a chart or table over it show REAL rows rather than an empty frame.
    bound = [
        *(doc["linked_datasets"] or []),
        *dataset_codes(schema.get("components") or []),
    ]
    builtin = [c for c in dict.fromkeys(bound) if c in BUILTIN_COLLECTIONS]
    declared = [c for c in bound if c not in BUILTIN_COLLECTIONS]
    # A bare `{% for x in y %}` source is a GUESS -- loop_sources cannot tell a dataset code from a
    # form answer or a context variable. Names the render context already provides are not datasets
    # at all, so drop them; whatever is left is resolved speculatively and can only ever be
    # advisory. (Failing the render on these breaks every template that loops over its own inputs.)
    provided = {
        *(doc["inputs"] or {}),
        *context,
        "answers",
        "datasets",
        "today",
        "document_name",
    }
    guessed = [
        c for c in loop_sources(schema.get("html")) if c not in provided and c not in declared
    ]
    # Reporting resolver, not the rows-only wrapper: a dataset that didn't resolve used to be a
    # log line the firm never saw, and the client got a document with silently empty tables.
    resolution = await resolve_datasets_reporting(
        session, job["tenant_id"], [*declared, *guessed], subject_id, speculative=set(guessed)
    )
    if resolution.blocking:
        raise DatasetUnresolved(resolution.blocking)
    # The built-ins join the resolved rows under their own code, so `substitute_components` renders
    # a chart or table over them from real data. A context key that is not a list of rows is
    # skipped rather than coerced — a component would render nonsense from it.
    rows = dict(resolution.rows)
    for code in builtin:
        value = context.get(code)
        if isinstance(value, list):
            rows[code] = value
    return {
        "doc": dict(doc),
        "context": context,
        "datasets": rows,
        "warnings": list(resolution.problems),
    }


async def _render_pdf(inputs: dict[str, Any]) -> tuple[bytes, str, str, list[str]]:
    """Render the document to a PDF. Pure of any DB session -- the CPU/subprocess work (Jinja +
    WeasyPrint/Chromium) must NOT run while holding a pooled connection open in a transaction.
    Returns (pdf_bytes, sha256, renderer_name, empty_fields) — the last being merge tokens that
    resolved to blank during rendering, so the persist step can surface them."""
    doc = inputs["doc"]
    schema = doc["schema"] or {}
    html = schema["html"]
    css = schema.get("css") or ""
    page_size = schema.get("pageSize")
    raw_inputs = doc["inputs"] or {}
    metadata = doc["metadata"] or {}
    datasets: dict[str, Any] = inputs.get("datasets") or {}
    variables: dict[str, Any] = {
        # Dataset rows exposed both top-level (so `{% for row in <code> %}` works) and grouped
        # under `datasets` (so `{{ datasets.<code> }}` works). Spread FIRST so adviser-entered form
        # answers and the client/adviser/firm context below win on any name clash.
        **datasets,
        **{str(k): v for k, v in raw_inputs.items()},
        **inputs["context"],
        "answers": raw_inputs,
        "datasets": datasets,
        # First row of each dataset for single-value bindings: `{{ one.<code>.<col> }}` (no loop).
        "one": first_rows(datasets),
        "today": date.today().isoformat(),
        # `date` is the sample/preview's prepared-date (formatted); the worker shipped only
        # `today` (ISO) before, so `{{ date }}` rendered blank.
        "date": date.today().strftime("%d %B %Y"),
        "document_name": doc["name"],
    }
    rendered_html, empty_fields = jinja_engine.render_with_diagnostics(str(html), variables)
    # Replace the chart/table markers with server-rendered SVG/HTML (WeasyPrint runs no JS, so the
    # editor's canvas preview can't carry over) — see app/render/components.py.
    rendered_html = substitute_components(rendered_html, schema.get("components"), datasets)
    renderer_name = (
        (metadata.get("renderer") if isinstance(metadata, dict) else None)
        or doc["renderer"]
        or "weasy"
    )
    pdf_bytes, sha = await get_renderer(renderer_name).to_pdf(rendered_html, css, "", page_size)
    return pdf_bytes, sha, renderer_name, empty_fields


async def _persist(
    session: AsyncSession,
    job: dict[str, Any],
    doc: dict[str, Any],
    key: str,
    pdf_bytes: bytes,
    sha: str,
    renderer_name: str,
    snapshot: dict[str, Any],
    warnings: list[DatasetProblem] | None = None,
    empty_fields: list[str] | None = None,
    bucket: str | None = None,
) -> bool:
    """Record the rendered PDF + mark the document generated, in one short transaction.

    Idempotent under visibility-timeout reclaim: the job is completed ONLY if it is still
    'processing' on THIS worker's claim (``attempts`` unchanged). If another worker reclaimed it
    (attempts bumped) we skip -- so a slow render that gets reclaimed never writes duplicate
    ``files``/audit rows. The S3 object was written under a deterministic key, so a double render
    just overwrites the same key (no orphan/dup)."""
    claimed = (
        await session.execute(
            text(
                "UPDATE documents.render_jobs SET status='done', updated_at=now() "
                "WHERE id=:id AND status='processing' AND attempts=:att RETURNING id"
            ),
            {"id": job["id"], "att": job["attempts"]},
        )
    ).scalar_one_or_none()
    if claimed is None:
        logger.info("render job %s reclaimed/completed elsewhere; skipping persist", job["id"])
        return False
    tenant_id = job["tenant_id"]
    file_id = (
        await session.execute(
            text(
                "INSERT INTO documents.files "
                "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                "VALUES (:t, :bucket, :key, 'application/pdf', :size, :sha) "
                "ON CONFLICT (bucket, storage_key) DO UPDATE SET "
                "  content_type = EXCLUDED.content_type, size_bytes = EXCLUDED.size_bytes, "
                "  sha256 = EXCLUDED.sha256 "
                "RETURNING id"
            ),
            {
                "t": tenant_id,
                "bucket": bucket or settings.s3_documents_bucket,
                "key": key,
                "size": len(pdf_bytes),
                "sha": sha,
            },
        )
    ).scalar_one()
    note = _warning_note(list(warnings or []))
    # Merge tokens that rendered blank (referenced but unsourced) — surfaced on the document so a
    # silent empty field is a visible advisory, not a mystery the adviser only spots by eye.
    if empty_fields:
        empties = "Merge fields with no value: " + ", ".join(empty_fields[:8])
        note = f"{note} · {empties}" if note else empties
    await session.execute(
        text(
            "UPDATE documents.documents "
            "SET status='generated', pdf_file_id=:fid, renderer_version=:rv, "
            "    dataset_snapshots=CAST(:snap AS jsonb), error=:note, generated_at=now() "
            "WHERE id=:doc"
        ),
        {
            "fid": file_id,
            "rv": renderer_name,
            "doc": job["document_id"],
            "snap": json.dumps(snapshot, default=str),
            "note": note,
        },
    )
    await session.execute(
        text(
            "INSERT INTO documents.audit_log (tenant_id, document_id, actor_id, action, detail) "
            "VALUES (:t, :doc, :actor, 'document.generated', CAST(:detail AS jsonb))"
        ),
        {
            "t": tenant_id,
            "doc": job["document_id"],
            "actor": doc["generated_by"],
            "detail": json.dumps(
                {
                    "renderer": renderer_name,
                    "version": int(doc["version_number"]),
                    **(
                        {"dataset_warnings": [p.detail for p in (warnings or [])]}
                        if warnings
                        else {}
                    ),
                    **({"empty_fields": empty_fields} if empty_fields else {}),
                }
            ),
        },
    )
    # NOW file the document under its client, which is what makes it visible to them (see
    # `documents/service.py::link_subject`). It happens HERE, on a successful render, and not where
    # the document was enqueued.
    #
    # The link is the whole of the client's read permission: the client branch of `documents_read`
    # (0021_documents_scope) matches on `contact_id` ALONE, with no status predicate, and
    # `list_documents`' default `status=active` is only `status <> 'archived'`. So linking at
    # enqueue time showed the client a document that had not rendered yet — and, if the render
    # failed, one that never would, sitting in their portal for ever with no adviser having
    # reviewed anything. That is the pre-render exposure 0021 was written to close.
    #
    # `intended_contact_id` is the right source: it has recorded "this document is FOR this client"
    # since 0021 while granting no visibility of its own, every filing path sets it, and reading it
    # here means the two enqueueing callers (documents/service.py::generate and the flow engine's
    # generate_document step) need no code of their own -- as does a document re-filed while it was
    # still queued. Same transaction as the `generated` write, so a visible document and a rendered
    # one commit together or not at all. No actor: nothing human performed this link.
    #
    # EXCEPT an illustration, unless the firm has asked for it (`shared_on_render`): a projection
    # nobody has reviewed was in the client's portal the moment it rendered (FB-20261006-04).
    # The document still carries its `intended_contact_id`, so staff share it from the record.
    if doc.get("intended_contact_id") is not None:
        from app.documents import service as docs_service

        if await docs_service.shared_on_render(
            session, tenant_id=tenant_id, document_id=job["document_id"]
        ):
            await docs_service.link_subject(
                session,
                tenant_id=tenant_id,
                document_id=job["document_id"],
                contact_id=int(doc["intended_contact_id"]),
            )
    # Close the flow engine's loop: a `generate_document` step is WAITING on this document and
    # nothing else can advance it. In the same transaction as the document write, so a rendered
    # document and a released step commit together or not at all. No-op for staff generation.
    await orchestrator.on_document_rendered(session, str(job["document_id"]))
    return True


async def _fail_or_retry(
    maker: async_sessionmaker[AsyncSession],
    job: dict[str, Any],
    error: str,
    *,
    permanent: bool = False,
) -> None:
    """Requeue with backoff, or dead-letter (job + document 'failed') once past max attempts.

    ``permanent`` DEAD-LETTERS ON THE FIRST ATTEMPT. Some failures cannot come good on a retry —
    a dataset code the platform does not provide is just as absent thirty seconds later — and
    retrying one only delays the report. The document sits at ``queued`` for the whole of a
    three-attempt backoff, so the operator watches a spinner for minutes before being told about
    something that was decided on attempt one. Worse, the honest answer was available immediately.

    A transient failure is the opposite and still retries: a lost connection, object storage
    refusing a put, a statement timeout. The distinction is what the exception TYPE says about
    whether the next attempt could differ, and it is drawn at the raise site rather than by
    inspecting messages here.
    """
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        if permanent or job["attempts"] >= job["max_attempts"]:
            await session.execute(
                text(
                    "UPDATE documents.render_jobs SET status='failed', last_error=:e, "
                    "  updated_at=now() WHERE id=:id"
                ),
                {"e": error, "id": job["id"]},
            )
            await session.execute(
                text("UPDATE documents.documents SET status='failed', error=:e WHERE id=:doc"),
                {"e": error, "doc": job["document_id"]},
            )
            # A flow step waiting on this document must FAIL with the render error, not wait
            # forever — that dead end is the whole reason this callback exists.
            await orchestrator.on_document_rendered(session, str(job["document_id"]), error=error)
            # Dead-letter: a document a user is waiting for will never render. Straight to the
            # error tracker — recovery needs a human.
            report_error(
                "render job dead-lettered",
                log=logger,
                event="render_job_dead_lettered",
                job_id=int(job["id"]),
                document_id=str(job["document_id"]),
                attempts=int(job["attempts"]),
                error=error,
                permanent=permanent,
            )
        else:
            await session.execute(
                text(
                    "UPDATE documents.render_jobs SET status='queued', last_error=:e, "
                    "  run_after = now() + make_interval(secs => :backoff), updated_at=now() "
                    "WHERE id=:id"
                ),
                {"e": error, "id": job["id"], "backoff": RETRY_BACKOFF_SECONDS},
            )
            logger.warning(
                "render job %s failed (attempt %s), retrying: %s",
                job["id"],
                job["attempts"],
                error,
            )


async def handle_once(maker: async_sessionmaker[AsyncSession]) -> bool:
    """Claim + process one job. Returns True if a job was handled, False if the queue was idle.

    Three phases so no pooled connection is held during the (multi-second) render: (1) read inputs,
    (2) render + upload the PDF holding NO DB connection, (3) persist in a short, idempotent txn."""
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        job = await _claim(session)
    if job is None:
        return False
    try:
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            inputs = await _load_inputs(session, job)
            # Resolved on the session that is already open: the render below is slow, and which
            # bucket a document belongs in must not depend on when the write finishes.
            bucket = await tenant_storage.document_bucket(session, int(job["tenant_id"]))
        doc = inputs["doc"]
        pdf_bytes, sha, renderer_name, empty_fields = await _render_pdf(inputs)
        key = storage.tenant_rendered_key(doc["uid"], int(doc["version_number"]), "pdf")
        await storage.put_document(key, pdf_bytes, "application/pdf", bucket=bucket)
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            rendered = await _persist(
                session,
                job,
                doc,
                key,
                pdf_bytes,
                sha,
                renderer_name,
                inputs.get("datasets") or {},
                inputs.get("warnings") or [],
                empty_fields,
                bucket,
            )
        if rendered:
            # Fold the completed render into the action stream (own service_role txn, best-effort):
            # the generate REQUEST is already an event, this is it actually rendering. Guarded on
            # _persist claiming the job — a reclaimed/duplicate render emits no second event.
            await record_document_action(
                event_type=DOCUMENT_RENDER_EVENT,
                method="POST",
                route="/documents/{uid}/render",
                document_uid=str(doc["uid"]),
                tenant_id=int(job["tenant_id"]),
                actor_id=str(doc["generated_by"]) if doc.get("generated_by") else None,
                actor_sub=WORKER_RENDER_SUB,
                extra={
                    "renderer": renderer_name,
                    "version": int(doc["version_number"]),
                    **({"empty_fields": empty_fields} if empty_fields else {}),
                },
                # Write through the worker's own maker (its DB), not app.db.SessionLocal.
                session_maker=maker,
            )
    except DatasetUnresolved as exc:
        # An author-facing message, not a stack-trace token: it is shown on the document and on the
        # dead-lettered step.
        #
        # PERMANENT. Every blocking dataset reason is a statement about the template or the
        # catalogue, not about this moment: a code that names no dataset, a dataset with no
        # published version, one that is disabled, or a per-client dataset on a document with no
        # client. None of those change because we waited. Retrying them held the document at
        # `queued` through the whole backoff, which is why a template bound to a code that no
        # longer exists read as "still working on it" for minutes instead of failing at once.
        await _fail_or_retry(maker, job, str(exc), permanent=True)
    except RenderImpossible as exc:
        # No retry: see the class. The message is already author-facing.
        await _fail_or_retry(maker, job, str(exc), permanent=True)
    except Exception as exc:  # noqa: BLE001 — any render/persist failure retries or dead-letters
        # RETRY is the default for anything unrecognised, and deliberately so: a storage blip, a
        # lost connection or a statement timeout all come good on the next attempt, and refusing
        # one on its first failure turns a slow document into a lost one.
        await _fail_or_retry(maker, job, f"{type(exc).__name__}: {exc}")
    return True


# ---- heartbeat --------------------------------------------------------------------------------


def heartbeat_path() -> Path:
    """Where the heartbeat lives. Inside the container, so the healthcheck (which runs *in* the
    container) can read it; the API is a different container and cannot — it watches queue depth
    via /metrics instead."""
    if settings.worker_heartbeat_path:
        return Path(settings.worker_heartbeat_path)
    return Path(tempfile.gettempdir()) / "graphene-render-worker.json"


class Heartbeat:
    """Per-loop liveness stamps, flushed to a small JSON file after every touch.

    Stamped at the START and END of each iteration: the gap between the two is the work, and the
    per-loop ``LOOP_STALL_SECONDS`` allowance is sized for the slowest legitimate iteration.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or heartbeat_path()
        self.started_at = time.time()
        self.stamps: dict[str, float] = {}

    def touch(self, loop: str) -> None:
        self.stamps[loop] = time.time()
        self.flush()

    def flush(self) -> None:
        payload = {"started_at": self.started_at, "loops": self.stamps}
        try:
            # Several local/prod worker processes may briefly overlap during a restart. A shared
            # ``beat.tmp`` lets one process replace another's temporary file, after which the loser
            # raises FileNotFoundError on every heartbeat. The final path is intentionally shared;
            # only each atomic write's staging name must be process-local.
            tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(self.path)  # atomic — the healthcheck never reads a half-written file
        except OSError as exc:
            # A heartbeat we can't write must not kill the worker; it degrades to "unhealthy",
            # which is the honest signal anyway.
            logger.warning(
                "could not write the worker heartbeat",
                extra={
                    "event": "heartbeat_write_failed",
                    "path": str(self.path),
                    "error": str(exc),
                },
            )


def healthcheck(path: Path | None = None, *, now: float | None = None) -> tuple[bool, str]:
    """Read the heartbeat and decide whether this worker is healthy.

    Unhealthy when the file is missing/unreadable (the process never got going, or died before its
    first iteration) or any loop's stamp is older than its allowance (that loop is wedged). Returns
    ``(healthy, reason)`` so the CLI can print something an operator can act on.
    """
    target = path or heartbeat_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"no readable heartbeat at {target}: {type(exc).__name__}"
    stamps = payload.get("loops") or {}
    at = now if now is not None else time.time()
    stale: list[str] = []
    for loop, allowance in LOOP_STALL_SECONDS.items():
        stamp = stamps.get(loop)
        if not isinstance(stamp, int | float):
            stale.append(f"{loop}: never ran")
        elif at - float(stamp) > allowance:
            stale.append(
                f"{loop}: last beat {int(at - float(stamp))}s ago (allowed {allowance:.0f}s)"
            )
    if stale:
        return False, "; ".join(stale)
    return True, "all loops beating"


# ---- the loops --------------------------------------------------------------------------------


async def _render_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """Drain documents.render_jobs. Its own task, so a slow PDF blocks nothing else.

    IT DOES NOT RUN WHERE IT CANNOT RENDER. WeasyPrint needs a native stack (Pango/Cairo/GLib) that
    installs on Linux and not on Windows, which is why the renderer is a container in dev while the
    Windows-native worker exists for the reactive loops beside it. Both poll this same queue with
    SKIP LOCKED, so a native worker that claimed a job took it FROM the container that could do it,
    failed on `cannot load library 'libgobject-2.0-0'`, and burned one of its three attempts —
    three of those dead-letter a document that was never un-renderable. The symptom is a document
    stuck at "queued" and a log line about a library, which reads as a broken template.

    So the loop asks the renderer whether it can load before claiming anything, and stands down
    loudly if not. The other loops carry on: notifications, the outbox and the automation consumer
    all work perfectly well on Windows, and they are why this worker runs there at all.
    """
    can_render, why = weasyprint_available()
    if not can_render:
        logger.warning(
            "render loop STOOD DOWN — this worker cannot load WeasyPrint's native stack, so it "
            "will not claim render jobs (they are left for the `render` container, which can). "
            "Every other loop in this worker is unaffected. Reason: %s",
            why,
            extra={"event": "render_loop_stood_down", "reason": why},
        )
        # The heartbeat still has to be touched or the healthcheck calls the container unhealthy for
        # a loop that is deliberately idle.
        while True:
            beat.touch(RENDER_LOOP)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    while True:
        set_correlation_id(None)
        beat.touch(RENDER_LOOP)
        try:
            did_work = await traced_job(RENDER_LOOP, handle_once(maker))
        except Exception as exc:  # noqa: BLE001 — a claim/DB blip must never kill the poll loop
            report_error(
                "render loop iteration failed; backing off and continuing",
                exc=exc,
                log=logger,
                event="render_loop_failed",
            )
            did_work = False
        beat.touch(RENDER_LOOP)
        if not did_work:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _tenant_data_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """Drain operations-only tenant reset/demo jobs one at a time."""
    while True:
        set_correlation_id(None)
        beat.touch(TENANT_DATA_LOOP)
        try:
            did_work = await traced_job(TENANT_DATA_LOOP, tenant_data.handle_once(maker))
        except Exception as exc:  # noqa: BLE001 - one reset must not kill the worker
            report_error(
                "tenant data job loop failed; continuing",
                exc=exc,
                log=logger,
                event="tenant_data_loop_failed",
            )
            did_work = False
        beat.touch(TENANT_DATA_LOOP)
        if not did_work:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


# `_instructions_loop` used to sit here, draining `platform.provider_instructions`. The queue is
# gone: it had a worker, retry classes, backoff, idempotency and tests, and `enqueue()` was called
# from nowhere but those tests -- a finished queue with no producer and no reader, the exact shape
# `pkgs/api-log` records `audit.api_log` having been in. The console's drawers call the vendor
# directly, which docs/provider-routing.md §6a argues for anyway (an operator watching a form is
# better served by the vendor's own 409 than by a job id). The vendor-safety knowledge it carried
# stays: `ActionDef.retry` still classifies every operation, and the drawer now warns before
# sending one that cannot be retried.


async def _outbox_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """The engine's transactional-outbox drain (task.requested → tasks, notify.* → email;
    docs/tasks.md). Its own short service_role txn per pass — splits into its own deployment unit
    if volume ever demands, the queue is the contract.

    Deliberately NOT gated on ``email.sending_disabled()`` the way ``_notifier_loop`` is: this loop
    also drains ``task.requested``, ``identity.check.requested`` and ``webhook_out``, none of which
    sends mail, so skipping it under suppression would stop a dev box raising flow tasks at all. The
    equivalent guard lives on the email ROW instead (``dispatcher._drain`` → held), where it can
    hold a ``notify.*`` row without touching anything else in the queue."""
    while True:
        set_correlation_id(None)
        beat.touch(OUTBOX_LOOP)
        try:
            dispatched = await traced_job(OUTBOX_LOOP, dispatcher.dispatch_once(maker))
        except Exception as exc:  # noqa: BLE001 — same discipline: never kill the loop
            report_error(
                "outbox dispatch failed; backing off and continuing",
                exc=exc,
                log=logger,
                event="outbox_dispatch_failed",
            )
            dispatched = False
        beat.touch(OUTBOX_LOOP)
        if not dispatched:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _comms_poll_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """The delivery SAFETY NET — ``app/comms/poll.py`` says why it exists and what it is not.

    Runs alongside the webhook rather than instead of it, because the webhook is the half that can
    stop silently. Off only when ``COMMS_POLL_SECONDS`` is 0, and then it RETURNS rather than
    idling, so a deployment that has genuinely turned it off carries no loop at all.

    It does NOT touch the heartbeat when it is off, and is not in ``LOOP_STALL_SECONDS`` unless it
    is on (see below): `healthcheck` reads a missing stamp as "never ran", so a loop that is
    deliberately disabled would otherwise make every webhook-configured deployment permanently
    unhealthy. An earlier version touched once on the way out and claimed that distinguished
    "off" from "wedged" — nothing read that stamp, so it distinguished nothing.
    """
    if settings.comms_poll_seconds <= 0:
        return
    while True:
        set_correlation_id(None)
        beat.touch(COMMS_POLL_LOOP)
        try:
            await traced_job(COMMS_POLL_LOOP, comms_poll.refresh_pending(maker))
        except Exception as exc:  # noqa: BLE001 — same discipline: never kill the loop
            report_error(
                "comms delivery poll failed; backing off and continuing",
                exc=exc,
                log=logger,
                event="comms_poll_failed",
            )
        beat.touch(COMMS_POLL_LOOP)
        await asyncio.sleep(settings.comms_poll_seconds)


# Watched only when it runs, and sized to the WORST HONEST PASS rather than to the cadence. A
# TYPICAL pass now costs ONE provider request — the list call answers for the whole recent page —
# but the worst one makes that call AND then falls back to `poll.BATCH` per-id reads SEQUENTIALLY
# at a 10s timeout each, for rows too old to be on that page. So the bound stays sized to the
# fallback plus the list call, which is deliberately conservative: three cadences (60s at the 20s
# default) is far less than that, and a slow Resend must not read as a wedged loop.
#
# The heartbeat is only touched between passes, so the tight allowance turned a
# provider slowdown into an unhealthy container, which compose restarts, taking the render, outbox,
# notifier, screening, automations and timers loops down with it. Whichever is larger, plus a
# cadence's slack: a wedged loop still shows, and a slow provider no longer escalates into a
# restart loop.
if settings.comms_poll_seconds > 0:
    # The list call's own timeout is a TERM, not slack: a worst pass makes that request AND then
    # falls back to `BATCH` per-id reads, so the count is one more than the per-id count.
    #
    # AN ESTIMATE OF THE DOMINANT TERM, NOT A PROOF, and worth saying because the previous version
    # of this comment claimed otherwise. `httpx.Timeout(10.0)` sets connect, read, write and pool
    # to ten seconds EACH — httpx has no total timeout, the gotcha `flows/lookup.py` answers with
    # two deadlines — so one request that connects slowly and then reads slowly can exceed its
    # nominal ten, and the up-to-`BATCH` ledger transactions are not counted here at all. The
    # figure is therefore the right order of magnitude rather than a ceiling; it is deliberately
    # generous against the CADENCE (three of those is 60s at the 20s default) because the failure
    # it guards is asymmetric — a wedged loop merely shows up late, while a false unhealthy
    # restarts the container and takes the render, outbox, notifier, screening, automations and
    # timers loops with it. If a real slow day ever crosses it, the fix is a total deadline around
    # the batch (lookup's shape), not a bigger number here.
    _poll_worst_pass = (comms_poll.BATCH + 1) * 10
    LOOP_STALL_SECONDS[COMMS_POLL_LOOP] = (
        max(settings.comms_poll_seconds * 3, _poll_worst_pass) + settings.comms_poll_seconds
    )

# The first pass for a newly registered environment can reconcile every membership and walk up to
# 200 retained event pages. It is async (so the other worker loops continue), but the heartbeat
# must allow that honest backfill while still detecting a permanently wedged vendor call.
if settings.workos_sync_poll_seconds > 0:
    LOOP_STALL_SECONDS[WORKOS_SYNC_LOOP] = 3600


async def _workos_sync_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """WorkOS-to-auth mirror safety net.

    This belongs in the worker, not uvicorn: reconciliation is background work, and an identity
    provider slowdown must never make the API's liveness endpoint or user requests unschedulable.
    The poller remains sequential per environment and keeps its transaction-scoped advisory lock,
    so overlapping worker generations or future replicas cannot apply the same cursor twice.
    """
    await poller.run_forever(maker, heartbeat=lambda: beat.touch(WORKOS_SYNC_LOOP))


async def _notifier_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """Task assign / near-due EVENTS — a slow scan (60s).

    It runs whatever the email configuration is. The scan no longer sends: it appends a
    ``task.assigned`` / ``task.due_soon`` row and stamps in the same transaction, so it is
    idempotent by stamp rather than by delivery, and the notices are now ordinary notifications —
    the in-app bell is driven by the same events and must not go dark because a dev box mails
    nobody. Suppression is the email tail's own business (``email.sending_disabled``), reported on
    ``/health/ready`` and ``/metrics``.
    """
    while True:
        set_correlation_id(None)
        beat.touch(NOTIFIER_LOOP)
        try:
            with job_span(NOTIFIER_LOOP):
                async with maker() as session, session.begin():
                    await session.execute(text("SET LOCAL ROLE service_role"))
                    await notifier.notify_once(session)
        except Exception as exc:  # noqa: BLE001 — same discipline: never kill the loop
            report_error(
                "task notification scan failed; continuing",
                exc=exc,
                log=logger,
                event="notifier_scan_failed",
            )
        beat.touch(NOTIFIER_LOOP)
        await asyncio.sleep(notifier.SCAN_INTERVAL_SECONDS)


async def _complycube_events_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """Apply stored ComplyCube webhooks (``compliance.events``) and route each status change, and
    every ``COMPLYCUBE_CHECK_POLL_SECONDS`` ask ComplyCube what has changed (``poller.py``) — the
    main path while webhooks are off.

    The receiver only stores the event; this loop fetches what it points at, writes
    ``compliance.*`` and routes the change (``app/complycube/consumer.py``). One pass is one
    transaction under a transaction-scoped advisory lock, so overlapping worker generations
    cannot apply the same batch twice. With ComplyCube unconfigured the loop no-ops and says so on
    a repeat, like ``_notifier_loop`` without RESEND_API_KEY.
    """
    last_notice = 0.0
    next_check_poll = 0.0
    while True:
        set_correlation_id(None)
        beat.touch(COMPLYCUBE_LOOP)
        full = False
        if not complycube_configured():
            if time.monotonic() - last_notice >= DISABLED_NOTICE_SECONDS or last_notice == 0.0:
                last_notice = time.monotonic()
                logger.warning(
                    "ComplyCube event consumer is DISABLED -- COMPLYCUBE_API_KEY is not "
                    "configured, so stored screening webhooks are not being applied",
                    extra={"event": "complycube_events_disabled"},
                )
        else:
            try:
                polled = None
                with job_span(COMPLYCUBE_LOOP):
                    async with maker() as session, session.begin():
                        await session.execute(text("SET LOCAL ROLE service_role"))
                        result = await complycube_consumer.drain_once(session, require_client())
                        # Under the consumer's lock, so a poll never records a change at the
                        # same time as a webhook does.
                        if result is not None and time.monotonic() >= next_check_poll:
                            next_check_poll = time.monotonic() + COMPLYCUBE_CHECK_POLL_SECONDS
                            polled = await complycube_poller.poll_once(session, require_client())
                            # The backstop for a step that finished without a check settling it (a
                            # cancelled journey): the identity photos held for it go.
                            await idv_held.release_finished(session)
                if polled is not None and (polled.changed or polled.failed):
                    logger.info(
                        "complycube checks asked about",
                        extra={
                            "event": "complycube_checks_polled",
                            "asked": polled.asked,
                            "changed": polled.changed,
                            "routed": polled.routed,
                            "failed": polled.failed,
                        },
                    )
                if result is not None and (result.applied or result.failed):
                    logger.info(
                        "complycube events applied",
                        extra={
                            "event": "complycube_events_applied",
                            "applied": result.applied,
                            "routed": result.routed,
                            "failed": result.failed,
                            "deferred": result.deferred,
                        },
                    )
                full = (
                    result is not None
                    and result.deferred == 0
                    and result.claimed >= complycube_consumer.BATCH
                )
            except Exception as exc:  # noqa: BLE001 — same discipline: never kill the loop
                report_error(
                    "ComplyCube event pass failed; continuing",
                    exc=exc,
                    log=logger,
                    event="complycube_events_failed",
                )
        beat.touch(COMPLYCUBE_LOOP)
        await asyncio.sleep(0 if full else COMPLYCUBE_POLL_SECONDS)


async def _firm_webhooks_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """Send the queued ``check.updated`` deliveries to firms' webhooks (``compliance.webhook_
    deliveries``, ``app/complycube/deliveries.py``).

    Each pass leases a batch in one short transaction, sends outside it, and records the outcomes
    in a second, so a slow firm endpoint never holds a row lock or a pooled connection. With
    ``SECRETS_MASTER_KEY`` unset no delivery can be signed: the loop no-ops and says so on a
    repeat, and the rows wait.
    """
    last_notice = 0.0
    while True:
        set_correlation_id(None)
        beat.touch(FIRM_WEBHOOKS_LOOP)
        full = False
        if not firm_deliveries.signing_configured():
            if time.monotonic() - last_notice >= DISABLED_NOTICE_SECONDS or last_notice == 0.0:
                last_notice = time.monotonic()
                logger.warning(
                    "firm webhook delivery is DISABLED -- SECRETS_MASTER_KEY is not configured, "
                    "so queued check.updated deliveries cannot be signed",
                    extra={"event": "firm_webhooks_disabled"},
                )
        else:
            try:
                result = await traced_job(FIRM_WEBHOOKS_LOOP, firm_deliveries.deliver_once(maker))
                if result.claimed:
                    logger.info(
                        "firm webhook deliveries attempted",
                        extra={
                            "event": "firm_webhooks_attempted",
                            "claimed": result.claimed,
                            "sent": result.sent,
                            "retrying": result.retrying,
                            "failed": result.failed,
                        },
                    )
                full = result.claimed >= firm_deliveries.BATCH
            except Exception as exc:  # noqa: BLE001 — same discipline: never kill the loop
                report_error(
                    "firm webhook pass failed; continuing",
                    exc=exc,
                    log=logger,
                    event="firm_webhooks_failed",
                )
        beat.touch(FIRM_WEBHOOKS_LOOP)
        await asyncio.sleep(0 if full else FIRM_WEBHOOKS_POLL_SECONDS)


async def _automations_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """The Flows v3 automation consumer — three passes, one loop (``app/flows/automation.py``).

    Every pass in the same task on purpose: they contend for the same rows (a deferred firing and
    a fresh event both settle on ``flows.automation_deliveries``, and both count against the same
    per-tenant rate window), so serialising them here is cheaper and far easier to reason about
    than three tasks racing for the same limits. The stream tail runs every pass; the schedule
    sweep is time-gated to once a minute inside it.

    Same failure discipline as the other four loops: a bad iteration is reported and the loop
    continues. A single firm's malformed automation must never stop every other firm's.

    **The platform kill switch is checked here, before any pass runs**
    (``app/flows/platform_pause.py``). All three passes are entered from this one place, so a gate
    here IS the enforcement — there is no other production caller. Deliberately not inside the
    consumer's per-candidate guard chain: the estate-wide switch is not a per-tenant decision, and
    an operator who has stopped the platform should not be paying for a candidate evaluation per
    event to find that out. The heartbeat is still touched, because a paused platform is a
    deliberate state, not a wedged container.
    """
    last_schedule = 0.0
    while True:
        set_correlation_id(None)
        beat.touch(AUTOMATIONS_LOOP)
        more = False
        try:
            async with maker() as session, session.begin():
                await session.execute(text("SET LOCAL ROLE service_role"))
                halted = await platform_pause.gate(session)
            if halted:
                beat.touch(AUTOMATIONS_LOOP)
                await asyncio.sleep(AUTOMATIONS_POLL_SECONDS)
                continue
            more = await traced_job(AUTOMATIONS_LOOP, automation.consume_once(maker))
            deferred = await traced_job(AUTOMATIONS_LOOP, automation.run_deferred_once(maker))
            more = more or deferred
            if time.monotonic() - last_schedule >= AUTOMATIONS_SCHEDULE_SECONDS:
                last_schedule = time.monotonic()
                await traced_job(AUTOMATIONS_LOOP, automation.schedule_once(maker))
        except Exception as exc:  # noqa: BLE001 — same discipline: never kill the loop
            report_error(
                "automation consumer iteration failed; backing off and continuing",
                exc=exc,
                log=logger,
                event="automations_loop_failed",
            )
            more = False
        beat.touch(AUTOMATIONS_LOOP)
        if not more:
            await asyncio.sleep(AUTOMATIONS_POLL_SECONDS)


async def _timers_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """The Flows v3 timer loop — wake the cases whose wait is over (``app/flows/timers.py``).

    Its own task, beside the automation consumer rather than inside it: the two touch different
    tables and a resumption is real engine work (steps instantiated, tasks raised), which has no
    business sitting behind a batch of stream rows. ``run_due_once`` claims each due timer with
    ``FOR UPDATE SKIP LOCKED`` and settles it in its own transaction, so several workers can run
    this concurrently without double-firing a wake-up.

    Same failure discipline as the other five: a bad iteration is reported and the loop continues.
    A single poisonous timer is dead-lettered by ``run_due_once`` itself and must never wedge every
    other firm's journeys behind it.
    """
    while True:
        set_correlation_id(None)
        beat.touch(TIMERS_LOOP)
        try:
            more = await traced_job(TIMERS_LOOP, timers.run_due_once(maker))
        except Exception as exc:  # noqa: BLE001 — same discipline: never kill the loop
            report_error(
                "flow timer loop iteration failed; backing off and continuing",
                exc=exc,
                log=logger,
                event="flow_timers_loop_failed",
            )
            more = False
        beat.touch(TIMERS_LOOP)
        if not more:
            await asyncio.sleep(TIMERS_POLL_SECONDS)


async def _baton_loop(engine: Any, beat: Heartbeat) -> None:
    """Claim a runnable baton run and advance it; sweep crashed runs when idle.

    The durable-action runtime (docs/baton.md) runs beside the legacy actions loop. It is quiet
    until a door creates a baton run, and it never kills the gather: one run's failure is recorded
    on that run and the loop continues.
    """
    from baton import Worker

    from app.actions.baton_engine import build_engine

    worker = Worker(
        build_engine(engine, default_budget_seconds=55.0), poll_seconds=BATON_POLL_SECONDS
    )
    while True:
        set_correlation_id(None)
        beat.touch(BATON_LOOP)
        more = False
        try:
            with job_span(BATON_LOOP):
                more = await worker.run_once()
        except Exception as exc:  # noqa: BLE001 — never kill the loop
            report_error(
                "baton run failed; backing off and continuing",
                exc=exc,
                log=logger,
                event="baton_loop_failed",
            )
            more = False
        beat.touch(BATON_LOOP)
        if not more:
            try:
                with job_span(BATON_LOOP):
                    await worker.sweep_once()
            except Exception as exc:  # noqa: BLE001
                report_error("baton sweep failed", exc=exc, log=logger, event="baton_sweep_failed")
            await asyncio.sleep(BATON_POLL_SECONDS)


async def _sei_receipts_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """Re-read SEI activities we have lodged and not yet matched.

    ``raw.api_transactions`` runs once each weekday morning and fans out across every
    account. This is the short loop: one GET per open activity, every fifteen minutes,
    writing SEI's state onto the Graphene row. It does not mark the money posted.
    """
    from app.integrations.sei.receipt_status import POLL_SECONDS, refresh_submitted

    while True:
        set_correlation_id(None)
        beat.touch(SEI_RECEIPTS_LOOP)
        try:
            with job_span(SEI_RECEIPTS_LOOP):
                async with maker() as session, session.begin():
                    await session.execute(text("SET LOCAL ROLE service_role"))
                    await refresh_submitted(session)
        except Exception as exc:  # noqa: BLE001 — one SEI miss must not kill the worker
            report_error(
                "SEI receipt refresh failed; backing off and continuing",
                exc=exc,
                log=logger,
                event="sei_receipts_loop_failed",
            )
        beat.touch(SEI_RECEIPTS_LOOP)
        await asyncio.sleep(POLL_SECONDS)


async def _notifications_loop(maker: async_sessionmaker[AsyncSession], beat: Heartbeat) -> None:
    """The in-app notifications tail — a second cursor consumer of ``audit.actions``
    (``app/notifications/consumer.py``), beside the automation consumer.

    Its own task rather than folded into ``_automations_loop``: the two tail the SAME stream but
    hold SEPARATE cursors and touch different tables, so a slow notification fan-out has no business
    sitting behind an automation batch (or vice versa). ``consume_once`` claims its cursor
    ``FOR UPDATE SKIP LOCKED``, so running several workers never double-consumes.

    Same failure discipline as the other loops: a bad iteration is reported and the loop continues;
    a single malformed action must never stop the estate's notifications. The estate-wide switch
    (``settings.notifications_enabled``) is checked inside ``consume_once``, which still advances
    the cursor while off — releasing it must not unleash a backlog.
    """
    # Start the clock at boot so the first repair pass is one interval away: the tail has just
    # opened and is about to read that window anyway, so sweeping immediately would duplicate it.
    last_resweep = time.monotonic()
    while True:
        set_correlation_id(None)
        beat.touch(NOTIFICATIONS_LOOP)
        try:
            more = await traced_job(NOTIFICATIONS_LOOP, notifications_consumer.consume_once(maker))
        except Exception as exc:  # noqa: BLE001 — same discipline: never kill the loop
            report_error(
                "notifications tail iteration failed; backing off and continuing",
                exc=exc,
                log=logger,
                event="notifications_loop_failed",
            )
            more = False
        beat.touch(NOTIFICATIONS_LOOP)
        # The REPAIR pass, on a slow cadence beside the tail. The tail reads with a LAG_SECONDS
        # window to avoid advancing its cursor past a row that has not committed yet — a bet on
        # commit duration which, when it loses, drops that notification silently and for ever
        # (see consumer.LAG_SECONDS). `resweep_once` re-reads the recent past and re-runs the same
        # fan-out; the deterministic `source_ref` unique index makes every already-delivered row a
        # no-op, so the healthy case is a read and no writes.
        #
        # In THIS loop rather than its own task: it shares the tail's cadence and failure rules,
        # and running it here means it cannot drift out of step with the cursor it exists to repair.
        # Its own try/except so a failed sweep never stops the tail — the repair is best-effort by
        # nature, and the next sweep covers the same window again.
        now = time.monotonic()
        if now - last_resweep >= NOTIFICATIONS_RESWEEP_SECONDS:
            last_resweep = now
            try:
                await traced_job(NOTIFICATIONS_LOOP, notifications_consumer.resweep_once(maker))
            except Exception as exc:  # noqa: BLE001 — a failed repair must not kill the tail
                report_error(
                    "notifications resweep failed; the tail continues and the next sweep retries",
                    exc=exc,
                    log=logger,
                    event="notifications_resweep_failed",
                )
            beat.touch(NOTIFICATIONS_LOOP)
        if not more:
            await asyncio.sleep(NOTIFICATIONS_POLL_SECONDS)


async def _email_notifications_loop(
    maker: async_sessionmaker[AsyncSession], beat: Heartbeat
) -> None:
    """The notification-EMAIL tail — the in-app tail's twin
    (``app/notifications/email_consumer.py``), its own cursor over the same stream. Renders branded
    notification emails and enqueues them to
    ``flows.outbox`` (the dispatcher sends them, so the non-prod send guard applies). Gated by
    ``settings.email_notifications_enabled`` inside ``consume_once``; inert until a category's email
    preference is turned on. Same never-die discipline as the other loops."""
    while True:
        set_correlation_id(None)
        beat.touch(EMAIL_NOTIFICATIONS_LOOP)
        try:
            more = await traced_job(
                EMAIL_NOTIFICATIONS_LOOP, notifications_email_consumer.consume_once(maker)
            )
        except Exception as exc:  # noqa: BLE001 — same discipline: never kill the loop
            report_error(
                "notification-email tail iteration failed; backing off and continuing",
                exc=exc,
                log=logger,
                event="email_notifications_loop_failed",
            )
            more = False
        beat.touch(EMAIL_NOTIFICATIONS_LOOP)
        if not more:
            await asyncio.sleep(EMAIL_NOTIFICATIONS_POLL_SECONDS)


async def run_forever() -> None:
    # A long-lived poller against a managed Postgres: the DB / proxy silently culls idle
    # connections, so validate before use (pool_pre_ping, as app/db.py does), retire old ones
    # (pool_recycle), and
    # bound every query (TCP keepalives + a server-side statement_timeout). Without these a dead
    # pooled connection makes the next poll BLOCK forever on a half-open socket — and a blocked call
    # never raises, so the loops' `except` can't catch it: the worker "hangs" until a restart (the
    # stall is in the idle poll path, not the render).
    engine = create_async_engine(
        settings.migration_database_url,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
            "options": "-c statement_timeout=30000",
        },
    )
    instrument_engine(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    beat = Heartbeat()

    # The outgoing-call audit, in THIS process. `app/main.py` installs one in the API's lifespan
    # and nothing installed one here -- so every vendor call the worker made fell back to
    # `default_recorder()`, which was None, and was recorded nowhere. `audit.api_log` is meant to
    # answer "what did we say to a third party, and what did it say back" for the whole platform,
    # and a gap that depends on WHICH PROCESS made the call is invisible from the log itself.
    #
    # Bound to this worker's OWN session factory rather than the module-level engine: the worker
    # connects on `migration_database_url`, which `app.db.system_transaction` does not use, and a
    # drain writing through a pool this process does not own is how a shutdown loses the tail.
    # `SET LOCAL ROLE service_role` for the same reason every other loop here does it.
    @asynccontextmanager
    async def _audit_session() -> AsyncIterator[AsyncSession]:
        async with maker.begin() as session:
            await session.execute(text("SET LOCAL ROLE service_role"))
            yield session

    outbound = api_log.ApiLog(session_factory=_audit_session)
    await outbound.start()
    api_log.install(outbound)
    set_default_recorder(outbound.recorder(source="app"))

    logger.info(
        "DocumentWorker started; polling documents.render_jobs + flows.outbox + task notices "
        "+ the WorkOS mirror + ComplyCube events + firm webhooks + the automation consumer "
        "+ flows.timers + the baton runnable-run queue + SEI receipt status",
        extra={"event": "worker_started", "heartbeat": str(beat.path)},
    )
    try:
        # gather, not a single serial loop: independent queues, independent cadences.
        # Each loop swallows its own errors, so gather only returns if one is cancelled.
        await asyncio.gather(
            _render_loop(maker, beat),
            _tenant_data_loop(maker, beat),
            _outbox_loop(maker, beat),
            _notifier_loop(maker, beat),
            _comms_poll_loop(maker, beat),
            _workos_sync_loop(maker, beat),
            _complycube_events_loop(maker, beat),
            _firm_webhooks_loop(maker, beat),
            _automations_loop(maker, beat),
            _timers_loop(maker, beat),
            _baton_loop(engine, beat),
            _sei_receipts_loop(maker, beat),
            _notifications_loop(maker, beat),
            _email_notifications_loop(maker, beat),
            _render_service_task(),
        )
    except asyncio.CancelledError:
        # SIGINT cancels this task. The count has to be cleared before the awaits below, or the
        # next one raises CancelledError immediately and the audit drain and the pool are abandoned.
        # Swallowing it here is the shutdown: asyncio.run would otherwise print the cancellation
        # and then raise KeyboardInterrupt.
        _clear_pending_cancellation()
    finally:
        # Before the engine goes: stop() drains what is already queued rather than discarding it,
        # and it writes through this engine.
        set_default_recorder(None)
        api_log.install(None)
        await outbound.stop()
        await engine.dispose()
    logger.info("DocumentWorker stopped", extra={"event": "worker_stopped"})


def _clear_pending_cancellation() -> None:
    """Drop this task's cancellation count so a shutdown await can finish."""
    task = asyncio.current_task()
    if task is None:
        return
    while task.cancelling():
        task.uncancel()


async def _render_service_task() -> None:
    """Serve the synchronous preview-render endpoint (``app.render_service``) so the API renders
    template previews on the SAME engine generation uses — here, in the worker — rather than needing
    WeasyPrint in the API process. Not a heartbeat loop (it has its own ``/internal/health``); bound
    to all interfaces so the API reaches it by service name (deploy) or published port (local).

    Fails soft, like the poll loops above (the ``gather`` invariant): a failure to bind or serve the
    preview port must NOT take down the worker's client-facing work (generation, outbox, notices,
    screening). Retries with backoff, then — if it still can't come up (e.g. the port is already
    bound) — logs and gives up, leaving the other loops running. Previews go unavailable, not
    generation."""
    config = uvicorn.Config(
        render_service.app,
        host="0.0.0.0",  # noqa: S104 — internal-only port; reached by the API over the compose network
        port=settings.render_service_port,
        log_level="warning",
        access_log=False,
    )
    delay = 2.0
    for attempt in range(1, 6):
        try:
            await uvicorn.Server(config).serve()
            return  # clean shutdown (cancellation / signal) — nothing to retry
        except asyncio.CancelledError:
            raise  # the worker is stopping; let gather unwind
        except Exception:
            logger.exception(
                "render service failed to serve (attempt %d/5); other worker loops continue",
                attempt,
                extra={"event": "render_service_error"},
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)
    logger.error(
        "render service gave up after repeated failures; previews unavailable until restart",
        extra={"event": "render_service_gave_up"},
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    init_tracing("portals-render")
    configure_logging(SERVICE_NAME)
    # WeasyPrint + fontTools emit a wall of routine INFO on every render (per-glyph subset dumps,
    # Step 1-7 layout progress) that buries the worker's own logs. Lift them to WARNING so genuine
    # problems still surface. (The recurring `word-break: break-word` warning is a template-CSS bug:
    # it should be `overflow-wrap: break-word`.)
    for _noisy in ("fontTools", "fontTools.subset", "weasyprint", "weasyprint.progress"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)
    if "--healthcheck" in args:
        try:
            healthy, reason = healthcheck()
            (logger.info if healthy else logger.error)(
                "worker healthcheck: %s", reason, extra={"event": "worker_healthcheck"}
            )
            return 0 if healthy else 1
        finally:
            shutdown_tracing()
    init_error_tracking(SERVICE_NAME)
    if sys.platform == "win32":  # async psycopg can't use the default ProactorEventLoop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        # A second Ctrl+C raises from the signal handler rather than as a cancellation. The
        # process is already leaving; a traceback here is the same noise as the first one.
        logger.info("DocumentWorker stopped", extra={"event": "worker_stopped"})
    finally:
        shutdown_tracing()
    return 0


if __name__ == "__main__":
    sys.exit(main())
