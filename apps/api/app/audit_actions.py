"""Action events — the request-level "every action emits an event" floor.

All three portals (client / platform / admin) act through this API, so the API's mutating surface
is the product's action surface. ``ActionEventsMiddleware`` appends one row to ``audit.actions``
(0041) for every authenticated mutating request (POST/PUT/PATCH/DELETE) that **succeeds** (2xx):
who (actor sub + resolved ``auth.users`` uuid), in what tenant context (including the
``?tenant=<uid>`` book/ops target, so a proxied middle-office action names BOTH the operator's
tenant and the firm acted on), which operation (``action.`` + the route's OpenAPI operation id),
against which targets (the matched path params — plus, for event types on the ``SUBJECT_FIELDS``
allowlist, the named id/enum subject fields out of the body/query, landing as
``metadata.subject``; plus, for update-shaped events on the ``PREVIOUS_FIELDS`` registry, the
row's before-values pre-read into ``metadata.previous`` so the trail can say "from what, to
what"), with what outcome (the response status).

Three deliberate design choices, so nobody has to re-litigate them:

* **An audit of effects, not attempts.** A failed action (4xx/5xx) emits nothing — matching the
  house precedent: the ``audit.record()`` DML triggers only ever see committed writes, and refused
  attempts land in structured logs, not the evidence tables. The middleware only observes the
  response status once the handler's transaction has committed (dependency teardown runs inside
  ``self.app``), so a 2xx here means the effect is durable.
* **Best-effort, post-commit — the event write must never break the action.** The response has
  already been sent when the row is written; any failure is swallowed into
  ``app.logging.report_error`` (it reaches the error tracker, never the caller). This is honest:
  the request-level floor is at-least-notified, not transactional. A subscriber that needs an
  event enqueued IN THE SAME TRANSACTION as its domain action uses a domain event on the flows
  outbox (``task.requested`` / ``identity.check.requested`` set the pattern), not this stream.
* **A trigger source, not just a trail.** ``audit.actions`` is shaped as a stream (monotonic
  ``id`` cursor, machine-routable ``event_type``, top-level filter columns) so a future worker
  loop or flow trigger can tail it — see docs/api.md → "Action events" for the consumer contract.

Coverage is enforced structurally by the tripwire test (``tests/test_actions_tripwire.py``):
every mutating route must carry ``get_current_principal`` in its dependency tree (any of the
session dependencies does) — which is also what stashes the principal where this middleware can
see it — or be named on ``EXEMPT_MUTATING_ROUTES`` below with a justification. A handler marked
``writes_no_row`` is transport, not an act: the middleware writes nothing, and the tripwire does
not demand an ``EVENT_TYPES`` entry. That mark is how the realtime ticket mints stay out of this
stream without being added to either exclusion list.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from fastapi.routing import APIRoute
from sqlalchemy import text

from app import db
from app.auth import Principal
from app.event_schema import registry as event_registry
from app.logging import current_correlation_id, report_error

logger = logging.getLogger(__name__)

#: Set when a handler writes the action event in the same transaction as the
#: mutation (four-eye lifecycle — ``review.requested`` / ``approved`` / …). The
#: middleware would otherwise emit a second, empty-subject row on the 2xx.
_lifecycle_event_written: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "lifecycle_event_written", default=False
)


def mark_lifecycle_event_written() -> None:
    _lifecycle_event_written.set(True)


def lifecycle_event_written() -> bool:
    return _lifecycle_event_written.get()


#: The verbs that mutate. HEAD/GET/OPTIONS never emit.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: The event-type namespace. The suffix is the route's OpenAPI operation id
#: (``{tag}-{route name}``, main.py's ``custom_generate_unique_id``) — a closed, stable
#: The prefix every event type carried before the ``resource.verb`` rename, kept for two jobs
#: and no others: the fallback in ``event_type_for`` for a route with no declared name, and
#: MATCHING HISTORY
#: — ``audit.actions`` is append-only, so every row written before the rename still starts with
#: this.
#: Anything that filters the stream by family has to accept both spellings or it silently stops
#: covering the past.
EVENT_PREFIX = "action."

# ---- the exemption list ----------------------------------------------------------------------
# (method, route template) pairs the guarantee deliberately does NOT cover. Every entry needs a
# reason; the tripwire test fails on a mutating route that is neither covered nor listed here,
# and on a stale entry that no longer matches a real route. Keep this list SHORT — it is the
# hole in the guarantee.
#
# The /internal/* endpoints are token-gated service-to-service calls from the portals' own
# servers (require_internal_token; no bearer token, no human principal to attribute — the acting
# human, when there is one, is pre-auth by definition: forgot-password and set-password happen
# before a session exists). Auth-plane actions that never reach this API at all (WorkOS-side
# login/MFA) are a known boundary, documented in docs/api.md → "Action events".
#
# The second (and only other) category is a POST that performs NOTHING — a question whose payload
# is too large for a query string, so it borrows the verb without borrowing the meaning. There is
# no effect to record, and the caller is an editor asking it on a debounce, so an event per call
# would fill a firm's activity log with keystrokes. Such an endpoint must still authenticate
# normally, and must be genuinely read-only in its own tests.
#
# The THIRD category is a VENDOR CALLBACK: an unauthenticated POST whose caller is a third party,
# not a person. There is no principal to attribute it to, and inventing one would put a made-up
# actor on the record; what it does is already evidenced where it belongs — `/webhooks/resend`
# advances a `comms.messages` row and writes its own `comms.message_events` entry. Such an endpoint
# must be safe to leave open on its own terms: Resend's is verified against its signing secret,
# and with no secret it is off.
#
# TWO routes qualify in the second category today. Flow validation ("would this draft publish?")
# and the upload dialog's filename→type SUGGESTION (`/documents/classify-type`: matches a filename
# against the tenant's tag list, no content, no LLM, NO SAVE — a hint the user confirms before
# uploading with the concrete tag). The category is not the same thing as its membership. The two
# dry runs beside flow validation are deliberately NOT in it: `POST /flows/{uid}/simulate` and
# `POST /automations/{uid}/test` also perform nothing, but they read one named CLIENT's facts, which
# is worth a line in a firm's activity log. classify-type reads only the tenant's tag list and a
# filename string — no client subject — so it stays out. "Performs nothing" is necessary for an
# exemption here, never sufficient; a genuine subject is the disqualifier.
EXEMPT_MUTATING_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/internal/forgot-password"),
        ("POST", "/internal/onboarding-complete"),
        ("POST", "/internal/auth-event"),
        ("POST", "/internal/rate-limit"),
        # Asks "would this draft publish?" and writes nothing — see flows.validate_flow_definition.
        ("POST", "/flows/{uid}/validate"),
        # The dry run beside it, and its own docstring is the argument: "A DRY RUN. Nothing is
        # performed, nothing is written, nothing is queued, nothing is sent." It walks a draft over
        # one subject to answer a question an author has about their own draft — authoring, like a
        # preview, and an author iterating on a branch produced a row per attempt.
        ("POST", "/flows/{uid}/simulate"),
        # A filename→document-type SUGGESTION for the upload dialog: no save, no client subject
        # (documents.classify_document_type). A row per keystroke/file-pick would be pure noise.
        ("POST", "/documents/classify-type"),
        # Resend's delivery events — the third category above: a vendor callback with no
        # principal. What it records is the comms.messages row it advances, and its own
        # comms.message_events entry, which is a better trail than an actor-less action would be.
        ("POST", "/webhooks/resend"),
        # GET-OR-CREATE, and almost always the GET. Opening a client's Messages tab calls this on
        # every page view (and again on every `invalidate`) to resolve the channel, so the stream
        # filled with "opened a conversation with X" rows for a conversation that had existed for
        # weeks — dozens per client per afternoon, crowding out the events somebody might actually
        # want to read. The same reason `/documents/classify-type` is here: a row per page view is
        # noise, not evidence.
        #
        # Nothing is lost. A conversation that is genuinely created lands an INSERT on
        # `messaging.conversations`, which 0080 attaches to the 0001 DML audit spine — actor and
        # timestamp included — and the first message sent records its own action event. What is
        # dropped is only the claim that a conversation was "opened" when it was merely looked up.
        ("POST", "/messaging/conversations"),
        # ---- READ STATE AND PREVIEWS: the browser's own bookkeeping, not an act ------------------
        # Same reason as the two above, applied to the rest of it. These fired on a page view, a
        # scroll, a bell being cleared or a dialog being opened - 436 rows in a 5,357-row table,
        # and not one of them answers a question anybody brings to an audit trail. "Craig dismissed
        # every notification" is a fact about Craig's browser.
        #
        # Nothing is lost that was ever evidence. A read receipt leaves `read_at` on the
        # notification row; a preview renders nothing and saves nothing. The historic rows stay in
        # `audit.actions` and are still readable - `ROUTINE_EVENT_TYPES` names their types so the
        # feed does not lead with them - and what stops is writing new ones.
        ("POST", "/messaging/thread/read"),
        ("POST", "/messaging/conversations/{conversation_uid}/read"),
        ("POST", "/notifications/{uid}/read"),
        ("POST", "/notifications/read-all"),
        ("POST", "/notifications/{uid}/dismiss"),
        ("POST", "/notifications/dismiss-all"),
        ("POST", "/templates/{uid}/preview"),
        ("POST", "/templates/{uid}/preview.pdf"),
        ("POST", "/email-templates/{uid}/preview"),
        ("POST", "/admin/tenants/{tenant_uid}/email-templates/{uid}/preview"),
        # A client's money-movement review screen: it answers "what would this do?" and records
        # nothing. The raise that follows writes `money_movement.created`.
        ("POST", "/crm/money/preview"),
        # Token-gated server-to-server call from the client portal, exchanging a handoff ticket
        # for a view-as session token (``require_internal_token``, same reasoning as /internal/*).
        # The human is already attributed: opening the session — ``POST /view-as/open/{uid}`` —
        # is a covered mutating route, and every action taken inside the session carries
        # ``metadata.view_as`` naming it.
        ("POST", "/view-as/redeem"),
        # The phone half of an identity-photo link. The client portal calls these with the
        # internal token, holding the session token in a header; there is no WorkOS principal
        # for the middleware to name. The act is still the client's: ``app/idv/phone.py``
        # writes ``journey.identity_photos_submitted`` against the person who asked for the
        # link. Redeeming the code mints that session
        # and changes nothing the client did — making the link (``POST …/identity-capture/phone``)
        # is the covered act.
        ("POST", "/identity-phone/redeem"),
        # What the phone has taken so far: progress for the waiting computer, no photo.
        ("POST", "/identity-phone/taken"),
        ("POST", "/identity-phone/submit"),
    }
)


def writes_no_row(endpoint: Any) -> Any:
    """This handler is transport, not an act. The middleware writes no ``audit.actions`` row.

    Named for what it DOES, because "action" is a product word now: `audit.actions` is read as the
    Activity log, so a decorator called `not_an_action` read as "keep this out of Activity" when
    what it means is "record nothing at all, anywhere".

    The mark lives on the endpoint. Realtime ticket mints are outside the action vocabulary:
    they are not entries on ``EXEMPT_MUTATING_ROUTES`` or ``ROUTINE_EVENT_TYPES``. The
    tripwire still requires every other mutating route to be declared or exempt, so forgetting
    a real action cannot ship as silence.
    """
    endpoint.__skip_action_event__ = True
    return endpoint


def skips_action_event(route: APIRoute) -> bool:
    return bool(getattr(route.endpoint, "__skip_action_event__", False))


# ---- redaction -------------------------------------------------------------------------------
# Request BODIES are never captured wholesale — that is the primary control. (The ONE exception
# is the explicit per-event-type subject allowlist below: named id/enum fields, filtered through
# this same redact belt.) What does land (path/query params) is still filtered: the exact keys
# mirror audit.record()'s redact list
# (0001, the 0038 password_ct addition, and 0186 secret_ct — keep them in step). The markers catch
# generic secret shapes (a query string carrying a token must never become evidence).
REDACTED_KEYS = frozenset(
    {
        "key_hash",
        "credentials_ref",
        "workos_api_key_ct",
        "cookie_password_ct",
        "password_ct",
        "secret_ct",
    }
)
#: Substring markers, matched case-insensitively. Deliberately NOT a bare "key": path params like
#: ``step_key`` are target identifiers the event exists to record.
REDACTED_MARKERS = ("password", "secret", "token", "authorization", "credential", "api_key")

REDACTED = "[redacted]"


def _secret_shaped(field: str) -> bool:
    """One definition of "this field name may carry a secret" — shared by `redact`,
    `extract_subject` and `capture_previous` so the three belts cannot drift apart."""
    lowered = field.lower()
    return field in REDACTED_KEYS or any(marker in lowered for marker in REDACTED_MARKERS)


# ---- subject capture -------------------------------------------------------------------------
# "Bodies are never captured" stays the PRIMARY control — this is the one, narrow exception to
# it: an explicit per-event-type ALLOWLIST of id-shaped body/query fields whose values name the
# event's SUBJECTS (which login was linked, which account was instructed, which template was
# rendered). Ids and closed enumerations AS A RULE — free text (names, emails, notes) is refused,
# and nothing on the redact list is admitted (enforced twice: the tripwire test refuses a
# secret-shaped entry, and `extract_subject` skips it at runtime even if one lands here by mistake
# — the belt). Amounts are included for the money-movement events as a deliberate judgement call:
# "who moved how much" is material to an audit trail in a way a phone number is not.
#
# ONE deliberate FREE-TEXT exception: the document-send covering note (`message`). It is the
# adviser's own words about what they sent, material to "what was done", so it belongs in the
# event's detail (a firm's own /events reads "shared a document, saying: …"). It is NOT a
# credential, and `redact()` masks secret-shaped KEYS only — so `message` lands verbatim, by
# design. This is the sole free-text field admitted here; add another only with the same reasoning.
#
# Captured best-effort into `metadata.subject`: a missing key is skipped, a non-JSON or oversized
# body is skipped silently, and the admin read surface (`GET /admin/audit/actions`) resolves the
# captured ids into display labels at READ time — so keep values here raw and stable.
# DECLARED ONCE in `app/event_schema/registry.py` (the event-schema registry) and projected here, so
# this allowlist, RESPONSE_SUBJECT_FIELDS and _FREE_TEXT_SUBJECT_FIELDS all read from ONE typed
# declaration instead of three hand-kept dicts. The registry also types each field (id/enum/amount)
# and names the entity kind of each id field, which the read side is pinned against. To change a
# captured field, edit the registry, not this module.
SUBJECT_FIELDS: dict[str, tuple[str, ...]] = event_registry.subject_fields()

#: Cap on how much request body the middleware will buffer looking for subject fields. Subject-
#: bearing bodies are small JSON; anything bigger (file uploads, template payloads) is not a
#: subject source, and the floor must never hold a large upload in memory twice.
MAX_SUBJECT_BODY_BYTES = 64 * 1024

#: Value guard for captured subjects: ids and closed enums are short scalars (or small lists of
#: them, e.g. `contact_ids`). A long string in an id/enum field means the field was mis-allowlisted
#: — drop the VALUE rather than trust it.
_MAX_SUBJECT_STR = 120
_MAX_SUBJECT_LIST = 100

#: The narrow set of DELIBERATELY free-text subject fields (the document-send covering note is the
#: only one). These are TRUNCATED, never dropped, at a larger cap — the id/enum "a long value means
#: mis-allowlisted, drop it" rule is wrong for a field whose whole point is prose, and a realistic
#: adviser note routinely exceeds 120 chars. Bounded so the floor never stores an unbounded body.
_FREE_TEXT_SUBJECT_FIELDS = event_registry.free_text_subject_fields()
_MAX_SUBJECT_TEXT = 500


def _subject_scalar_ok(value: Any) -> bool:
    if isinstance(value, bool | int | float):
        return True
    return isinstance(value, str) and len(value) <= _MAX_SUBJECT_STR


def _subject_value_ok(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) <= _MAX_SUBJECT_LIST and all(_subject_scalar_ok(v) for v in value)
    return _subject_scalar_ok(value)


def extract_subject(
    event_type: str, body: bytes, query_params: dict[str, str]
) -> dict[str, Any] | None:
    """The allowlisted subject fields of one event, best-effort.

    Reads ONLY the keys `SUBJECT_FIELDS` names for this event type, from the JSON body first and
    the query string second. Everything about this is fail-quiet by design: no allowlist entry,
    a body that isn't a JSON object, a missing key, an over-long value — each just yields less
    (or no) subject, never an error and never a fallback to capturing something broader.
    """
    fields = SUBJECT_FIELDS.get(event_type)
    if not fields:
        return None

    data: dict[str, Any] = {}
    if body:
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                data = parsed
        except (ValueError, UnicodeDecodeError):
            pass  # multipart/no-JSON bodies simply carry no subject

    subject: dict[str, Any] = {}
    for field in fields:
        if _secret_shaped(field):
            # The belt: even a mistakenly-allowlisted secret-shaped field never lands. The
            # tripwire test refuses such an entry at review time; this refuses it at runtime.
            continue
        value = data.get(field, query_params.get(field))
        if value is None:
            continue
        if field in _FREE_TEXT_SUBJECT_FIELDS:
            # A deliberate free-text field (the covering note): TRUNCATE, never drop — the id/enum
            # length guard would silently vanish a realistic note, defeating the whole point of
            # capturing it (a firm's /events reads "shared a document, saying: …").
            if isinstance(value, str) and value:
                subject[field] = (
                    value
                    if len(value) <= _MAX_SUBJECT_TEXT
                    else value[: _MAX_SUBJECT_TEXT - 1] + "…"
                )
            continue
        if not _subject_value_ok(value):
            continue
        subject[field] = value
    return subject or None


# ---- changed-key capture (WHICH fields an update carried, never their values) -------------------
# `SUBJECT_FIELDS` is an allowlist of VALUES: affordable for the twenty acts that turn on a closed
# enum, unaffordable for the forty update routes whose body IS the change. A client PATCH declares
# thirty optional fields and sends the two that moved, so "Updated a client" read the same whether
# an email or a date of birth changed.
#
# So this records the KEY NAMES and NEVER a value — the one rule that makes it safe to turn on
# broadly. Both bounds are against a CRAFTED key rather than a realistic one, because a key is
# caller-supplied text: `_CHANGED_KEY_RE` admits identifier-shaped names only, and
# `MAX_CHANGED_KEYS` refuses (never truncates) a body wider than any update form.
CHANGED_KEY_EVENTS: frozenset[str] = event_registry.changed_key_events()

#: A field name, as every pydantic model in the estate spells one. Anything else is not a field
#: this application declares, so recording it would be recording the caller's own text.
_CHANGED_KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")

#: More top-level keys than any update body has. Past it the body is not an update form and the
#: capture is abandoned whole, rather than a truncated list implying those were all the changes.
MAX_CHANGED_KEYS = 60


def extract_changed_keys(event_type: str, body: bytes) -> list[str] | None:
    """The NAMES of the top-level keys an update body carried, sorted — or None.

    Fail-quiet like both other captures. Values are never read, so this cannot leak one.
    """
    if event_type not in CHANGED_KEY_EVENTS:
        return None
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None  # multipart / non-JSON bodies carry no field list
    if not isinstance(parsed, dict):
        return None
    keys = sorted(k for k in parsed if isinstance(k, str) and _CHANGED_KEY_RE.match(k))
    if not keys or len(keys) > MAX_CHANGED_KEYS:
        return None
    return keys


# ---- what the save actually moved (the CHANGED_COLUMNS registry) --------------------------------
# `metadata.changed` names WHICH fields a request carried and `metadata.body` what it asked for.
# Neither answers WHAT IT USED TO BE, and they cannot between them: a body key is a WIRE name where
# the target is a COLUMN (`notes` against `vulnerability_notes`), and a body states an INTENTION
# where the row states the OUTCOME.
#
# So an update may declare the ROW it addresses, and the middleware reads that row twice — on the
# body hook (the last moment it holds its pre-action state, the timing `PREVIOUS_FIELDS` already
# relies on) and after a 2xx. What lands is the DIFFERENCE. It stays affordable because it is the
# diff rather than the row, both reads fail quiet, the redact belt runs on the column NAME before
# the comparison, and an oversized value is stored as its shape.
#
# The disclosure it adds is the OLD value of a field whose NEW value the row already stores, so it
# is the same data under the same access. The PII belt below narrows the worst of it.


@dataclass(frozen=True)
class RowSpec:
    """The one row an update addresses.

    ``table`` and ``key`` are interpolated, so both are checked against `_IDENTIFIER_RE` at import
    — a typo is an ImportError. ``param`` is the path param that names the row, and it BINDS;
    ``cast`` is for the one key here that is not text (`auth.users.id` is a uuid).
    """

    table: str
    key: str
    param: str
    cast: str | None = None


#: A schema-qualified table, or a bare column. Deliberately narrow: lower case, no quoting, no
#: expression — everything a legitimate entry needs and nothing an injected fragment could use.
_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")

#: Columns every write moves and nobody reads as a change. Deliberately short: a denylist is a
#: place for facts to hide, so a column earns its place here by being pure bookkeeping.
_NEVER_DIFFED = frozenset({"updated_at", "synced_at"})

#: Ceiling on ONE recorded value, past which its shape is recorded instead: a `theme` or a `fees`
#: column is a whole document, and two side by side is not a change anybody can see.
MAX_CHANGED_VALUE_CHARS = 512

#: Ceiling on the whole diff. A save that moved forty columns is a bulk edit, and the first few
#: plus an honest count of the rest says more than a truncated wall.
MAX_STORED_CHANGES_CHARS = 4 * 1024

#: The reserved key the overflow count is recorded under — no real column can be named this.
OMITTED_CHANGES_KEY = "_omitted"

#: An update-shaped event -> the row it addresses. EVERY entry addresses exactly ONE row by ONE
#: path param; that is the whole eligibility rule. It is why the settings events are absent (their
#: rows are keyed by the SESSION's tenant or user, which the pre-read cannot see — the same reason
#: `PREVIOUS_FIELDS` grew its `per_tenant` shape) and why `provider.updated` is (not pinned by one
#: column).
CHANGED_COLUMNS: dict[str, RowSpec] = {
    "account.bank_accounts_updated": RowSpec("platform.accounts", "uid", "account_uid"),
    "account.fee_packages_updated": RowSpec("platform.accounts", "uid", "account_uid"),
    "account.regular_movements_updated": RowSpec("platform.accounts", "uid", "account_uid"),
    "automation.updated": RowSpec("flows.automations", "uid", "uid"),
    "client.bank_account_updated": RowSpec("crm.contacts", "uid", "contact_uid"),
    "client.updated": RowSpec("crm.contacts", "uid", "contact_uid"),
    "client.vulnerability_updated": RowSpec("crm.contacts", "uid", "contact_uid"),
    "content.updated": RowSpec("content.items", "uid", "uid"),
    "dataset.updated": RowSpec("documents.datasets", "uid", "uid"),
    "document.updated": RowSpec("documents.documents", "uid", "uid"),
    "document_request.updated": RowSpec("documents.requests", "uid", "req_uid"),
    "document_type.updated": RowSpec("documents.document_types", "uid", "type_uid"),
    "email_template.updated": RowSpec("email.templates", "uid", "uid"),
    "fee_package.updated": RowSpec("platform.fee_packages", "uid", "fee_uid"),
    "firm.updated": RowSpec("auth.tenants", "uid", "tenant_uid"),
    "flow.updated": RowSpec("flows.flows", "uid", "uid"),
    "form.fields_updated": RowSpec("documents.forms", "uid", "uid"),
    "form.updated": RowSpec("documents.forms", "uid", "uid"),
    "member.profile_updated": RowSpec("auth.users", "id", "user_id", cast="uuid"),
    "model.updated": RowSpec("platform.models", "uid", "model_uid"),
    "notification_rule.updated": RowSpec("notifications.rules", "uid", "uid"),
    "product.updated": RowSpec("platform.products", "uid", "product_uid"),
    "task.updated": RowSpec("tasks.tasks", "uid", "uid"),
    "template.updated": RowSpec("documents.templates", "uid", "uid"),
}

for _event, _spec in CHANGED_COLUMNS.items():
    if not _IDENTIFIER_RE.match(_spec.table) or not _IDENTIFIER_RE.match(_spec.key):
        raise ValueError(f"CHANGED_COLUMNS[{_event!r}] names an unsafe identifier")


async def read_row(event_type: str, path_params: dict[str, Any]) -> dict[str, Any] | None:
    """The addressed row as JSON, or None — best-effort, everything fails quiet.

    ``to_jsonb`` rather than a column list, so the DIFF decides what is worth recording and no
    list has to be maintained per event. The row is transient. Runs as ``service_role`` for
    `capture_previous`' reason: evidence capture, not an access decision.
    """
    spec = CHANGED_COLUMNS.get(event_type)
    if spec is None:
        return None
    value = path_params.get(spec.param)
    if value is None or not isinstance(value, str | int):
        return None
    bind = f"CAST(:key AS {spec.cast})" if spec.cast else ":key"
    try:
        async with db.SessionLocal() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            rows = (
                (
                    await session.execute(
                        # noqa: S608 — `table`/`key` are registry constants checked against
                        # `_IDENTIFIER_RE` at import; the only caller-supplied value binds.
                        text(
                            f"SELECT to_jsonb(t) AS row FROM {spec.table} t "  # noqa: S608
                            f"WHERE t.{spec.key} = {bind} LIMIT 2"
                        ),
                        {"key": str(value)},
                    )
                )
                .scalars()
                .all()
            )
    except Exception:  # noqa: BLE001 — fail-quiet by contract; never block the action
        logger.debug("row read failed for %s — no change diff", event_type, exc_info=True)
        return None
    if len(rows) != 1 or not isinstance(rows[0], dict):
        return None  # zero rows: nothing to compare; >1: ambiguous — either way, no guess
    return rows[0]


def _bounded_value(value: Any) -> Any:
    """One side of one change, or its SHAPE where it is too big to be read as a value."""
    if value is None or isinstance(value, bool | int | float):
        return value
    rendered = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(rendered) > MAX_CHANGED_VALUE_CHARS:
        return f"[{len(rendered)} characters]"
    return value


def diff_rows(
    event_type: str, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> dict[str, dict[str, Any]] | None:
    """The columns that MOVED, each as ``{"from": …, "to": …}`` — or None for "nothing to say".

    Compared only where both reads carry the column, it is not bookkeeping and its NAME is not
    secret-shaped. Ordered by name; an overflowing tail is dropped WITH a count.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    changes: dict[str, dict[str, Any]] = {}
    spent = 0
    dropped = 0
    for column in sorted(set(before) & set(after)):
        if column in _NEVER_DIFFED or _secret_shaped(column):
            continue
        was, now = before[column], after[column]
        if was == now:
            continue
        pair = {"from": _bounded_value(was), "to": _bounded_value(now)}
        cost = len(json.dumps({column: pair}, default=str))
        if spent + cost > MAX_STORED_CHANGES_CHARS:
            dropped += 1
            continue
        changes[column] = pair
        spent += cost
    if dropped:
        changes[OMITTED_CHANGES_KEY] = {"from": None, "to": f"{dropped} further columns changed"}
    return changes or None


# ---- body capture (the whole request, redacted) -------------------------------------------------
# THE WIDEST CAPTURE, AND A DELIBERATE CHANGE OF DOCTRINE. "Bodies are never captured" held while
# the trail's job was "what was done, to which record". It cannot meet "enough to say exactly WHAT
# was done": for eighty-eight events the deciding content is free text, a form's answers or a
# nested object, and no allowlist of VALUES reaches those.
#
# Three properties keep it affordable: DECLARED per event, never blanket; REDACTED always, by
# `redact_deep`, so an event whose body is only credentials records nothing and stays unreplayable;
# and BOUNDED, a body past `MAX_STORED_BODY_BYTES` stored as a marker rather than truncated JSON.
#
# The consequence, stated rather than discovered: a client's own data now lands in `audit.actions`,
# which every holder of `activity:read` can read. Bounded by existing access, but wider than the
# rule it replaced. The PII belt below covers the worst fields.
BODY_CAPTURE_EVENTS: frozenset[str] = event_registry.body_capture_events()

#: Ceiling on the STORED body. Past it the row records that a body was sent and how big it was,
#: never a truncated object — `audit.actions` is evidence, and half a document read back as if it
#: were the whole one is a wrong answer told confidently.
MAX_STORED_BODY_BYTES = 8 * 1024


def redact_deep(value: Any, depth: int = 0) -> Any:
    """`redact`, applied all the way down.

    The flat version is right for path and query params. A body nests, and a credential does not
    become safe one level in — `{"secrets": {"workos_api_key": …}}` is the shape this exists for.
    """
    if depth > 6:
        return "[deep]"
    if isinstance(value, dict):
        return {
            k: (REDACTED if _secret_shaped(str(k)) else redact_deep(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_deep(v, depth + 1) for v in value]
    return value


def extract_body(event_type: str, body: bytes) -> dict[str, Any] | None:
    """The declared body of one event, redacted and bounded — or None.

    Fail-quiet: not declared, not a JSON object, or past the ceiling (which yields a marker, so
    the row still says a body was sent).
    """
    if event_type not in BODY_CAPTURE_EVENTS or not body:
        return None
    if len(body) > MAX_STORED_BODY_BYTES:
        return {"_omitted": "body too large to record", "_bytes": len(body)}
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None  # multipart / non-JSON: nothing to record, and nothing pretends otherwise
    if not isinstance(parsed, dict):
        return None
    redacted = redact_deep(parsed)
    return redacted or None


# ---- response subject capture (the created entity's id, which is RESPONSE-only) ----------------
# `SUBJECT_FIELDS` names subjects the CALLER supplied in the request. A create names its subject
# only in the RESPONSE — the new row's id — and a multipart upload's form fields never reach
# `extract_subject` at all (it parses JSON, and the body cap drops a large upload). This second,
# equally narrow allowlist lifts a named id field OUT of the 2xx JSON response and maps it to the
# READ side's own subject key (admin._ENTITY_FIELD_KINDS), so the trail names the created entity
# exactly as if the caller had passed its id: "Created a client" becomes a chip on the real person.
#
# Same discipline as SUBJECT_FIELDS — ids only, the redact belt applies, values guarded — plus one
# more: the response body is TEE'd (every chunk still flows to the client) and bounded, so this can
# never swallow a streaming response or hold a large body. Maps RESPONSE key → SUBJECT key.
# Also DECLARED in `app/event_schema/registry.py` (RESPONSE-source fields) and projected here — the
# same single source as SUBJECT_FIELDS. A created row names its subject only in the 2xx body (per
# doctrine above): firm-create-contact lifts the response `id` as `contact_id` (the highest-value
# gap in the stream); the two invites lift the response `user_id` so "Invited a member" names the
# invited login; files-upload-file echoes the filed contact past an unreadable multipart request.
RESPONSE_SUBJECT_FIELDS: dict[str, dict[str, str]] = event_registry.response_subject_fields()

#: Cap on how much RESPONSE body to buffer for the allowlist above. A created-record JSON is small;
#: past the cap the capture is abandoned (the floor must never hold a large body in memory).
MAX_RESPONSE_SUBJECT_BYTES = 64 * 1024


def extract_response_subject(event_type: str, body: bytes) -> dict[str, Any] | None:
    """The allowlisted RESPONSE subject fields of one event, best-effort — the same fail-quiet
    contract as `extract_subject`: no allowlist entry, a body that isn't a JSON object, a missing
    key or an over-long value each just yields less (or no) subject, never an error."""
    mapping = RESPONSE_SUBJECT_FIELDS.get(event_type)
    if not mapping:
        return None
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    subject: dict[str, Any] = {}
    for response_key, subject_key in mapping.items():
        if _secret_shaped(subject_key):
            continue  # the belt, as extract_subject
        value = _response_value(parsed, response_key)
        if value is None or not _subject_value_ok(value):
            continue
        subject[subject_key] = value
    return subject or None


def _response_value(parsed: dict[str, Any], response_key: str) -> Any:
    """One allowlisted value out of a parsed 2xx body, by a key or a `list[].field` path.

    A create that answers with ONE record has its id at a top-level key, which is all this
    needed at first. An upload answers with a LIST — `{"documents": [{...}, {...}]}`, because one
    request may drop several files — and each id is a field of an element. Without the path form
    the only liftable value is the list of whole objects, which `_subject_value_ok` rightly refuses
    (a subject holds short scalars, never rows), so the created documents could not be named at all
    and every upload event was about nothing.

    Nothing is coerced: a missing key, a non-list where a path expects one, or an element with no
    such field each yields less — the same fail-quiet contract as the rest of subject capture.
    """
    if "[]." not in response_key:
        return parsed.get(response_key)
    list_key, _, field = response_key.partition("[].")
    items = parsed.get(list_key)
    if not isinstance(items, list):
        return None
    values = [
        item[field] for item in items if isinstance(item, dict) and item.get(field) is not None
    ]
    return values or None


# ---- before-values (the PREVIOUS_FIELDS registry) ----------------------------------------------
# `metadata.subject` answers "to what"; this answers "FROM what". An update-shaped event on this
# registry gets a pre-read: one cheap primary-key SELECT, run in its own short `service_role`
# transaction BEFORE the handler overwrites the row (the read rides the request-body `receive`
# hook — FastAPI fully reads the body before solving dependencies or running the endpoint, so at
# that moment the route is stamped in the scope and the row still holds its pre-action state).
# The captured columns land as `metadata.previous`, so the trail can say "Adviser → Compliance"
# rather than just "Compliance".
#
# The same discipline as SUBJECT_FIELDS: an explicit per-event-type allowlist of id/enum-shaped
# columns, never free text, never anything secret-shaped (`capture_previous` re-checks the redact
# belt at runtime; the tripwire suite refuses a spec that names a redacted field, a stale event
# type, a parameter the route doesn't declare, or a body-less route the hook can never fire for).
# Capture is best-effort and FAIL-QUIET: any pre-read failure — bad SQL, junk path param, zero or
# ambiguous rows, DB down — just yields no `previous`, never an error and never a blocked action.
#
# Deliberate NON-entries, so nobody re-litigates them:
# * `client.portal_user_linked` (and the other link/create events) — a creation has no prior
#   state to record; the subject already says what was linked.
# * `client.bank_account_updated` — not because the values are withheld (they are NOT; see
#   `CHANGED_COLUMNS` below, which diffs the row) but because a named-field pre-read would be a
#   second, narrower copy of what that diff already records.


@dataclass(frozen=True)
class PreviousSpec:
    """One update-shaped event type's pre-read.

    ``sql`` selects exactly the ``fields`` columns (plus ``tenant_id`` when ``per_tenant``),
    binding only ``params`` — path-param names, resolved from the routed scope. A ``per_tenant``
    spec is for routes whose path params don't pin the tenant (the firm router scopes by the
    SESSION's tenant, which isn't resolved yet at pre-read time): it returns one candidate row
    per tenant, and ``pick_previous`` chooses at write time once ``auth.resolve_claims`` has
    named the acting/target tenant."""

    fields: tuple[str, ...]
    sql: str
    params: tuple[str, ...]
    per_tenant: bool = False


#: Cap on per-tenant candidate rows. A slug shared by more tenants than this means the pre-read
#: would be holding a cross-estate map in memory per request — skip instead (fail-quiet). The
#: per-tenant SQL LIMITs at a literal cap+1 (26 — literal so ruff's S608 sees no string
#: construction; the tripwire test pins the literal to this constant) so "too many" is
#: detectable without pulling the estate.
MAX_PREVIOUS_CANDIDATES = 25

PREVIOUS_FIELDS: dict[str, PreviousSpec] = {
    # -- member portal role: what role the person held BEFORE ("Adviser → Compliance") -----------
    "member.portal_role_updated": PreviousSpec(
        # A LIST, matching the shape the request sends (`role_slugs`): a person may hold several
        # roles in one portal, so a single slug was already lossy - and the read side pairs before
        # with after BY NAME, so the two halves have to be spelled the same or the row shows a
        # "was" with nothing beside it.
        fields=("role_slugs",),
        # ONE spec for BOTH doors, and it has to be the per-tenant one. The firm route's path names
        # the person and the portal but not the tenant (that comes from the session), while the ops
        # route's path names all three — so a spec that binds `tenant_id` works for one door and
        # cannot bind for the other. Reading every tenant's row for this (user, portal) and letting
        # `pick_previous` choose covers both, and it resolves correctly for the ops door because
        # that
        # door now records `target_tenant_id` (the firm it was done TO), which `pick_previous`
        # prefers.
        #
        # There used to be two entries here, one per door, and Python kept only the LAST — so the
        # firm
        # door was silently running the ops spec, whose `:tenant_id` param its path does not have.
        # Collapsing the two event types into one act is what made the clash visible.
        sql=(
            "SELECT m.tenant_id, array_remove(array_agg(r.slug), NULL) AS role_slugs "
            "FROM auth.organisation_memberships m "
            "LEFT JOIN auth.roles r ON r.id = m.role_id "
            "WHERE m.user_id = CAST(:user_id AS uuid) AND m.portal = :portal "
            "GROUP BY m.tenant_id "
            "LIMIT 26"
        ),
        params=("user_id", "portal"),
        per_tenant=True,
    ),
    # -- role permissions: the prior slug list, so the read side can diff added/removed ----------
    "role.permissions_updated": PreviousSpec(
        fields=("permissions",),
        # One spec for both doors, per-tenant, for exactly the reason given above: the firm route's
        # path carries the role slug alone.
        sql=(
            "SELECT r.tenant_id, "
            "  COALESCE(array_agg(p.slug ORDER BY p.slug) FILTER (WHERE p.slug IS NOT NULL), "
            "           CAST('{}' AS text[])) AS permissions "
            "FROM auth.roles r "
            "LEFT JOIN auth.role_permissions rp ON rp.role_id = r.id "
            "LEFT JOIN auth.permissions p ON p.id = rp.permission_id "
            "WHERE r.slug = :slug GROUP BY r.tenant_id "
            "LIMIT 26"
        ),
        params=("slug",),
        per_tenant=True,
    ),
    # -- contribution / transfer status: the trail reads "Matched -> Lodged", not "a status" ----
    # Both are keyed on the route's own `uid` path param, which is a global primary key, so there
    # is no tenant ambiguity and no per-tenant candidate sweep.
    "contribution.status_updated": PreviousSpec(
        fields=("status",),
        sql="SELECT status FROM platform.contributions WHERE uid = :uid",
        params=("uid",),
    ),
    "transfer.status_updated": PreviousSpec(
        fields=("status",),
        sql="SELECT status FROM platform.transfers WHERE uid = :uid",
        params=("uid",),
    ),
    # Re-classifying a credit is the act 0051's own notes call out as expensive to get wrong (a
    # wrong category reclaims relief from HMRC against a payment that was something else), so the
    # category it was moved AWAY from is the half the row was missing.
    "contribution.categorised": PreviousSpec(
        fields=("category",),
        sql="SELECT category FROM platform.contributions WHERE uid = :uid",
        params=("uid",),
    ),
    # -- account mandate: the prior model, so the trail reads "Cautious → Balanced" --------------
    "account.model_updated": PreviousSpec(
        fields=("model_id",),
        # The account id is a global primary key — no tenant ambiguity even on the proxied
        # (?tenant=) book path.
        # Keyed on the uid, because that is the path param the route declares — the pre-read binds
        # by NAME, and `test_actions_tripwire` fails a spec that names one the route does not.
        sql="SELECT model_id FROM platform.accounts WHERE uid = :account_uid",
        params=("account_uid",),
    ),
}

#: What `capture_previous` hands forward: candidate rows keyed by tenant id (per-tenant specs) or
#: by ``None`` (specs whose params already pin the row). `pick_previous` chooses at write time.
CapturedPrevious = dict[int | None, dict[str, Any]]


async def capture_previous(event_type: str, path_params: dict[str, Any]) -> CapturedPrevious | None:
    """The registered pre-read for one event, best-effort — everything fails quiet.

    Runs in its own short ``service_role`` transaction (the row may be RLS-scoped; the pre-read
    is evidence capture, not an access decision — the handler's own authorisation still decides
    whether the action happens at all). Any failure — junk path param that breaks a CAST, a
    missing row, an ambiguous row set, the DB being down — yields ``None``: the action must
    never be blocked or slowed beyond this one SELECT for the sake of a nicer audit sentence."""
    spec = PREVIOUS_FIELDS.get(event_type)
    if spec is None:
        return None
    try:
        async with db.SessionLocal() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            rows = (
                (
                    await session.execute(
                        text(spec.sql), {p: path_params.get(p) for p in spec.params}
                    )
                )
                .mappings()
                .all()
            )

        def captured_row(mapping: Any) -> dict[str, Any]:
            # The belt, same as extract_subject: a secret-shaped column never lands even if a
            # spec mistakenly names one (the tripwire refuses it at review; this at runtime).
            return {f: mapping[f] for f in spec.fields if not _secret_shaped(f)}

        if spec.per_tenant:
            if not rows or len(rows) > MAX_PREVIOUS_CANDIDATES:
                return None
            return {int(r["tenant_id"]): captured_row(r) for r in rows}
        if len(rows) != 1:
            return None  # zero rows: nothing existed; >1: ambiguous — either way, no guess
        return {None: captured_row(rows[0])}
    except Exception:  # noqa: BLE001 — fail-quiet by contract; never block the action
        logger.debug("previous-value pre-read failed for %s — skipped", event_type, exc_info=True)
        return None


def pick_previous(
    captured: CapturedPrevious | None, *, tenant_id: int | None, target_tenant_id: int | None
) -> dict[str, Any] | None:
    """Choose the one row `metadata.previous` records, once the tenant context is resolved.

    Proxied target first (a ``?tenant=`` book/ops action changes THAT firm's row), then the
    actor's own tenant, then the ``None`` key (specs whose params already pinned the row). No
    match — e.g. a per-tenant spec whose candidates don't include the resolved tenant — means no
    ``previous``: degrade, never guess."""
    if not captured:
        return None
    for key in (target_tenant_id, tenant_id, None):
        row = captured.get(key)
        if row:
            return row
    return None


def redact(params: dict[str, Any]) -> dict[str, Any]:
    """Replace any secret-shaped value with a marker. Keys survive (an operator can still see
    THAT a field was sent); values never do."""
    return {key: (REDACTED if _secret_shaped(key) else value) for key, value in params.items()}


def operation_id_for(route: APIRoute) -> str:
    """``<tag>-<name>``, the readable operation id — used for BOTH the OpenAPI spec and the event
    vocabulary, because they have to be the same string.

    Computed here rather than read off ``route.unique_id``, and that is the fix for a silent
    regression FastAPI 0.141 introduced. ``include_router`` used to COPY a router's routes onto
    the app, which is when the app's ``generate_unique_id_function`` was applied; 0.141 wraps
    the router instead, so a route object keeps the ``unique_id`` its own router gave it — FastAPI's
    default (``update_tenant_admin_tenants__tenant_id__patch``). The OpenAPI spec stayed correct,
    because that is generated with the app's function at spec-build time, so nothing visible broke:
    the generated TS client was fine and every request still worked.

    What broke was the AUDIT STREAM, silently. `event_type` would have changed shape for every route
    at once, which (a) severs continuity with every row already written, (b) stops
    `describeActionEvent`'s closed vocabulary matching, (c) stops the automations trigger tier
    resolving, and (d) — the one that matters — stops
    `routers/firm.py::FIRM_EXCLUDED_EVENT_PREFIXES` matching `action.aml-`, so Graphene's AML review
    activity would have appeared in the firm's own audit view. That is the withholding posture
    docs/aml.md calls a tipping-off control, defeated by a dependency bump.

    So the vocabulary is derived from the route's OWN tag and name, which are ours and declared in
    the decorator. An explicit ``operation_id=`` still wins, as it did before.
    """
    if route.operation_id:
        return route.operation_id
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}-{route.name}"


#: THE EVENT VOCABULARY: a route's operation id -> the event type it emits.
#:
#: An event type names WHAT HAPPENED as ``resource.verb`` — a singular domain noun, then what became
#: of it, in the past tense because an event has already happened
#: (``document.sent``, ``client.bank_account_updated``, ``email_template.version_published``).
#:
#: WHY THIS IS DECLARED RATHER THAN DERIVED. It used to be ``"action." + operation_id``, and the
#: operation id is ``{router tag}-{route name}`` — so the vocabulary was a description of the CODE'S
#: SHAPE rather than of the domain, and it read like one:
#:
#:   * the router tag leaked in, twice where a handler repeated it: ``action.admin-admin-create-
#:     email-
#:     template``, ``message.sent``. A reader cannot guess those, and the
#:     tag tells them nothing they wanted to know.
#:   * THE SAME ACT GOT TWO NAMES when it had two doors. Graphene publishing a firm's email
#:     template emitted a different type from the firm publishing its own, so every subscriber
#:     had to know both — and a rule written against one silently missed half the publishes.
#:     Who acted, and whether it was Graphene on the firm's behalf, is already on the row
#:     (``actor_id``, ``target_tenant_id``, ``route``); a second event type carries no
#:     information and splits every consumer.
#:   * renaming a HANDLER renamed an event. The vocabulary is a public contract — subscribers
#:     route on it — and it moved whenever someone tidied a function name.
#:
#: So the map is the contract, and two doors onto one act are simply two keys with one value.
#: ``tests/test_actions_tripwire.py`` fails if a mutating route has no entry, so a new endpoint
#: cannot land outside the vocabulary or quietly fall back to the old derived shape.
#:
#: RENAMING ONE IS A BREAKING CHANGE for anything that stored or subscribed to the old string. Rows
#: already in ``audit.actions`` keep the name they were written with — the table is append-only by
#: design — so a reader of history must accept both spellings (see ``LEGACY_EVENT_TYPES`` and the
#: two-spelling lists in ``audit_stream``/``routers/firm``).

EVENT_TYPES: dict[str, str] = {
    # -- Actions
    "actions-cancel-action-run": "action_run.cancelled",
    "actions-decide-action-run": "action_run.decided",
    "actions-remind-action-approvers": "action_run.reminded",
    "actions-retry-action-run": "action_run.retried",
    "actions-retry-action-step": "action_run.step_retried",
    "actions-set-action-policy": "action_policy.updated",
    "actions-start-action-run": "action_run.started",
    # -- Accounts
    "crm-account-drawdown-session": "account.drawdown_session_started",
    # Its own event, not `client.bank_account_updated`: the account's details and the client's are
    # different facts that resolve in that order, and an audit reader has to be able to tell which
    # one a withdrawal was paid from.
    "crm-set-account-bank-accounts": "account.bank_accounts_updated",
    "crm-set-account-fee-packages": "account.fee_packages_updated",
    "crm-set-account-regular-movements": "account.regular_movements_updated",
    "crm-set-account-model": "account.model_updated",
    # -- Partner API access (a firm's keys and its check webhook, managed by Graphene)
    "admin-create-api-key": "api_key.created",
    "admin-revoke-api-key": "api_key.revoked",
    "admin-set-tenant-webhook": "webhook.updated",
    "admin-delete-tenant-webhook": "webhook.removed",
    "admin-resend-webhook-delivery": "webhook.delivery_resent",
    # -- Automations
    "automations-create-automation": "automation.created",
    "automations-delete-automation": "automation.deleted",
    "automations-disable-automation": "automation.disabled",
    "automations-enable-automation": "automation.enabled",
    "admin-set-automation-platform-pause": "automation.platform_pause_updated",
    "automations-reorder-automations": "automation.reordered",
    "automations-set-automation-settings": "automation.settings_updated",
    "automations-test-automation": "automation.tested",
    "automations-update-automation": "automation.updated",
    # -- Branding
    "firm-upload-branding-asset": "branding.asset_uploaded",
    "admin-upload-branding-asset": "branding.asset_uploaded",
    "firm-set-branding": "branding.updated",
    "admin-set-branding": "branding.updated",
    # -- Catalogue
    "catalogue-create-fee-package": "fee_package.created",
    "catalogue-update-fee-package": "fee_package.updated",
    "catalogue-create-commentary": "model.commentary_created",
    "catalogue-update-commentary": "model.commentary_updated",
    "catalogue-create-model": "model.created",
    "catalogue-update-model": "model.updated",
    "admin-set-model-sei-dsrs": "model.sei_dsrs_updated",
    "catalogue-create-model-version": "model.version_created",
    "catalogue-publish-model-version": "model.version_published",
    "catalogue-create-product": "product.created",
    "catalogue-set-product-platform": "product.platform_reference_updated",
    "catalogue-update-product": "product.updated",
    "catalogue-create-provider": "provider.created",
    "catalogue-update-provider": "provider.updated",
    "catalogue-set-term": "terminology.updated",
    # -- Clients
    "firm-assign-adviser": "client.adviser_assigned",
    "firm-unassign-adviser": "client.adviser_unassigned",
    "firm-archive-contact": "client.archived",
    "crm-set-contact-bank-account": "client.bank_account_updated",
    # Checking a sort code and account number writes nothing, but it is still an act: someone
    # asked whether a pair is a real UK account. The digits stay out of the trail.
    "ref-check-bank-account": "bank_account.checked",
    "firm-create-contact": "client.created",
    "firm-add-contact-note": "client.note_added",
    "firm-delete-contact-note": "client.note_deleted",
    "firm-invite-contact-to-portal": "client.portal_invited",
    "firm-link-contact-user": "client.portal_user_linked",
    "firm-unlink-contact-user": "client.portal_user_unlinked",
    "crm-push-contact-to-morgan-lloyd": "client.pushed_to_morgan_lloyd",
    "firm-restore-contact": "client.restored",
    "crm-sync-contact-to-morgan-lloyd": "client.synced_to_morgan_lloyd",
    # The account record's own push. A distinct event from the client-wide one because it names a
    # different subject: what was created is this account's scheme and policy, and an audit row
    # saying "client synced" would not say which account an operator acted on.
    "crm-sync-account-to-morgan-lloyd": "account.synced_to_morgan_lloyd",
    # Filing a valuation is an act ON THE ACCOUNT, so it is named for the account rather than
    # for the client the surrounding routes are about.
    "crm-account-morgan-lloyd-valuation": "account.valuation_filed_at_morgan_lloyd",
    # The platform portal's "Link to IO" cards -- one write each, so each gets its own name rather
    # than collapsing onto the admin tab's combined write below (that one writes BOTH in a single
    # call, which is a different act, not the same act through a second door).
    "crm-link-contact-to-io": "client.linked_to_io",
    "crm-link-account-to-io": "account.linked_to_io",
    "firm-update-contact": "client.updated",
    "crm-set-contact-vulnerability": "client.vulnerability_updated",
    # Filing the signed W-9 / W-8 against the client's FATCA answer. Its own door and its own
    # name, as bank details and vulnerability have; the file itself is also `document.uploaded`
    # in the document's own trail.
    "crm-upload-contact-fatca-document": "client.fatca_document_uploaded",
    # A member of staff opening — and later leaving — this client's own portal (app/view_as.py).
    # Recorded as client events because the client is who the act was about; the actor is the
    # member of staff, and the reason they gave travels on the session row beside it.
    "view-as-open-view-as": "client.portal_viewed_as",
    "view-as-end-view-as": "client.portal_view_ended",
    # A nomination names a THIRD PARTY and a share of the fund, so who added or
    # withdrew one is a question a file review asks by name.
    "crm-add-account-beneficiary": "account.beneficiary_added",
    "crm-remove-account-beneficiary": "account.beneficiary_removed",
    # A client book decides which portal a client sees and whether they may move their own money
    # (docs/client-classifications.md), so editing the catalogue is a control change, named for the
    # book. Pinning is named for the CLIENT: it overrides the rules for one person, and that is the
    # question a file review asks — who put this client in the self-serve book.
    "crm-create-client-classification": "client_book.created",
    "crm-update-client-classification": "client_book.updated",
    "crm-delete-client-classification": "client_book.deleted",
    "crm-pin-contact-classification": "client.book_pinned",
    "crm-create-client-group": "client_group.created",
    "crm-update-client-group": "client_group.updated",
    "crm-delete-client-group": "client_group.deleted",
    "crm-set-contact-groups": "client.groups_set",
    # -- Contributions
    "contributions-update-contribution-application": "contribution.application_updated",
    "contributions-cancel-contribution": "contribution.cancelled",
    "contributions-categorise-contribution": "contribution.categorised",
    "contributions-create-contribution": "contribution.created",
    "contributions-lodge-contribution-expectation": "contribution.expectation_lodged",
    "contributions-run-contribution-matcher": "contribution.matcher_ran",
    "contributions-record-contribution-to-morgan-lloyd": "contribution.recorded_at_morgan_lloyd",
    "contributions-refresh-contribution-from-morgan-lloyd": (
        "contribution.refreshed_from_morgan_lloyd"
    ),
    "contributions-set-contribution-status": "contribution.status_updated",
    "contributions-stop-contribution-at-morgan-lloyd": "contribution.stopped_at_morgan_lloyd",
    "contributions-attach-contribution-transaction": "contribution.transaction_attached",
    "contributions-classify-transaction": "contribution.transaction_classified",
    "contributions-detach-contribution-transaction": "contribution.transaction_detached",
    # -- Datasets
    "datasets-create-dataset": "dataset.created",
    "datasets-delete-dataset": "dataset.deleted",
    "datasets-update-dataset": "dataset.updated",
    "datasets-add-dataset-version": "dataset.version_added",
    # -- Document templates
    "templates-create-template": "template.created",
    "templates-delete-template": "template.deleted",
    "templates-duplicate-template": "template.duplicated",
    "templates-unarchive-template": "template.unarchived",
    "templates-update-template": "template.updated",
    "templates-publish-template-version": "template.version_published",
    "templates-save-template-version": "template.version_saved",
    # -- Documents
    "documents-archive-document": "document.archived",
    "documents-cancel-document-render": "document.render_cancelled",
    "documents-generate-document": "document.generated",
    # NOT collapsed onto `document.generated`, though it is the same kind of work. The middleware
    # emits ONE row per request, so a 250-client batch is one event — a subscriber keyed on
    # `document.generated` would fire once for the whole batch and be unable to say which client it
    # was about. A separate name states what actually happened.
    "documents-generate-documents-batch": "document.batch_generated",
    "documents-retry-document": "document.render_retried",
    # TWO LEGACY NAMES, NO LIVE ROUTE. This act was `send`, was renamed to `notify`, and has now
    # been removed outright (a document REQUEST does the same job and records whether the client
    # acted). Neither operation id is on the route table any more, so neither maps anything live —
    # they stay because the UI's `LEGACY_EVENT_TYPES` is generated from this map, and dropping them
    # would leave every row either route ever wrote rendering as a raw id.
    #
    # `test_every_mutating_route_has_a_declared_event_type` only requires that a LIVE route has a
    # name, so an entry with no route is allowed here; nothing asserts the converse, because a
    # vocabulary that could not outlive its routes would be useless to an append-only stream.
    "documents-send-document": "document.sent",
    "documents-notify-document": "document.notified",
    "documents-unarchive-document": "document.unarchived",
    "documents-update-document": "document.updated",
    "documents-upload-document": "document.uploaded",
    "documents-cancel-document-request": "document_request.cancelled",
    "documents-create-document-request": "document_request.created",
    # -- Fund look-through reference data
    "look-through-admin-import-look-through": "fund_composition.imported",
    "documents-submit-request-form-response": "document_request.form_submitted",
    "documents-save-request-form-response": "document_request.form_saved",
    "documents-fulfill-request-item": "document_request.item_fulfilled",
    "documents-reassign-request-item": "document_request.item_reassigned",
    "documents-update-document-request": "document_request.updated",
    "shares-create-document-share": "document_share.created",
    "shares-revoke-share": "document_share.revoked",
    "documents-create-document-type": "document_type.created",
    "documents-update-document-type": "document_type.updated",
    # -- Email templates
    "email-templates-create-email-template": "email_template.created",
    "email-templates-delete-email-template": "email_template.deleted",
    # FOUR LEGACY NAMES, NO LIVE ROUTE — the same case as `documents-send-document` above.
    # Archiving an email template is gone: a firm DELETES one, which also gives its code back,
    # where an archived row went on holding it. The entries stay because the UI's
    # `LEGACY_EVENT_TYPES` is generated from this map, and dropping them would leave every row
    # either route ever wrote rendering as a raw id.
    "email-templates-archive-email-template": "email_template.archived",
    "admin-admin-archive-email-template": "email_template.archived",
    "email-templates-unarchive-email-template": "email_template.unarchived",
    "admin-admin-unarchive-email-template": "email_template.unarchived",
    "admin-admin-create-email-template": "email_template.created",
    "admin-delete-email-template": "email_template.deleted",
    "admin-admin-email-templates-load-defaults": "email_template.defaults_loaded",
    "admin-admin-email-templates-provision": "email_template.preset_provisioned",
    # Its OWN act, not `branding.asset_uploaded`: same bucket prefix by the same call, but the act
    # is putting a picture in an email, gated on `email_templates:write` — an author who may edit
    # the firm's emails deliberately cannot change the logo on its login page.
    "email-templates-upload-email-template-image": "email_template.image_added",
    "admin-upload-email-template-image": "email_template.image_added",
    "email-templates-send-test-email-template": "email_template.test_sent",
    "admin-admin-send-test-email-template": "email_template.test_sent",
    "email-templates-update-email-template": "email_template.updated",
    "admin-admin-update-email-template": "email_template.updated",
    "email-templates-publish-email-template-version": "email_template.version_published",
    "admin-admin-publish-email-template-version": "email_template.version_published",
    "email-templates-restore-email-template-version": "email_template.version_restored",
    "admin-admin-restore-email-template-version": "email_template.version_restored",
    "email-templates-save-email-template-version": "email_template.version_saved",
    "admin-admin-save-email-template-version": "email_template.version_saved",
    # Who dev and staging may email. The addresses themselves stay off the subject: they are
    # personal, and the row is the record. `use_default` is the closed decision — reset, or a
    # saved list (including a saved empty one, which mails nobody).
    "admin-set-outbound-email-allowlist": "email.allowlist_updated",
    "admin-register-sender-domain": "email.sender_domain_registered",
    "admin-set-sender-address": "email.sender_address_updated",
    "admin-verify-sender-domain": "email.sender_domain_verified",
    "admin-refresh-sender-domain": "email.sender_domain_refreshed",
    "admin-delete-sender-domain": "email.sender_domain_removed",
    "admin-send-sender-test": "email.sender_test_sent",
    # -- Client portal content (news / articles / PDF guides)
    "content-create-content-category": "content_category.created",
    "content-create-content-item": "content.created",
    "content-update-content-item": "content.updated",
    "content-publish-content-item": "content.published",
    "content-archive-content-item": "content.archived",
    "content-upload-content-guide": "content.guide_uploaded",
    "content-upload-content-hero": "content.hero_uploaded",
    "content-upload-content-image": "content.image_added",
    # -- Environments
    "admin-register-environment": "environment.registered",
    "admin-refresh-environment-registry": "environment.registry_refreshed",
    "admin-set-environment-secrets": "environment.secrets_updated",
    "admin-set-environment-applications": "environment.applications_updated",
    # -- Exports
    # A listing leaving the platform as a file is data egress, recorded for all three formats
    # (the portals build them; see app/routers/exports.py) rather than only the PDF, the one
    # format that used to pass through a render worker here.
    "exports-record-export": "export.listing_exported",
    # -- Files
    "files-delete-file": "file.deleted",
    "files-upload-file": "file.uploaded",
    # -- Firms
    "admin-set-tenant-ai": "firm.ai_settings_updated",
    "admin-set-tenant-currency-settings": "firm.currency_settings_updated",
    "admin-create-tenant": "firm.created",
    "admin-delete-tenant": "firm.deleted",
    "admin-create-tenant-data-job": "firm.data_reset_requested",
    "admin-refresh-tenant-provider": "firm.provider_data_refreshed",
    "admin-resume-tenant-providers": "firm.provider_refresh_resumed",
    "admin-set-tenant-provider": "firm.provider_updated",
    "admin-sync-tenant-roles": "firm.roles_synced",
    "admin-provision-tenant-storage": "firm.storage_provisioned",
    "admin-provision-all-tenant-storage": "firm.storage_provisioned_for_all",
    "admin-sync-tenant": "firm.synced",
    "admin-update-tenant": "firm.updated",
    "admin-refresh-tenant-usage": "firm.usage_refreshed",
    # -- Flows
    "flows-create-flow": "flow.created",
    # TEMPORARY, with the route (admin.admin_reseed_onboarding_flow): re-running the built-in
    # onboarding seed over an existing firm. Delete this line when the route goes.
    "admin-reseed-onboarding-flow": "flow.reseeded",
    "flows-replay-dead-letter": "flow.dead_letter_replayed",
    "flows-update-flow": "flow.updated",
    "flows-import-flow": "flow.imported",
    "flows-publish-flow-version": "flow.version_published",
    "flows-save-flow-version": "flow.version_saved",
    # -- Forms
    "forms-create-form": "form.created",
    "forms-delete-form": "form.deleted",
    "forms-set-form-fields": "form.fields_updated",
    "forms-publish-form": "form.published",
    "forms-create-form-response": "form.response_submitted",
    "forms-unarchive-form": "form.unarchived",
    "forms-update-form": "form.updated",
    # -- Illustrations
    "crm-create-illustration": "illustration.created",
    "crm-create-investment-illustration": "illustration.investment_created",
    # One illustration, shown to its client. Not a send: no email, and no recipient row.
    "crm-share-illustration": "illustration.shared",
    # The reference a GIA or ISA illustration copies onto itself. Graphene-wide rates and the
    # ISA allowance, and the per-firm disclosure, fees, custody footer and model mix.
    "admin-create_growth_set": "illustration.growth_set_created",
    "admin-create_isa_allowance": "illustration.isa_allowance_created",
    "admin-create_custody": "illustration.custody_created",
    "admin-import_instrument_costs": "illustration.instrument_costs_imported",
    "admin-put_disclosure": "illustration.disclosure_updated",
    "admin-create_fee_schedule": "illustration.fee_schedule_created",
    "admin-put_model_facts": "illustration.model_facts_updated",
    # -- Instructions
    "firm-cancel-instruction": "instruction.cancelled",
    "firm-create-instruction": "instruction.created",
    # The client portal's own door (`POST /crm/money`). Its preview writes no row
    # (`EXEMPT_MUTATING_ROUTES`); `money_movement.previewed` rows already written are history.
    "crm-client-money-cancel": "money_movement.cancelled",
    "crm-client-money-raise": "money_movement.created",
    # -- Journeys
    "applications-cancel-application": "journey.cancelled",
    "applications-create-application": "journey.created",
    "applications-start-application": "journey.started",
    "applications-assign-application": "journey.assigned",
    "applications-action-application-step": "journey.step_actioned",
    "applications-submit-application-step-form": "journey.step_form_submitted",
    "applications-save-application-step-form": "journey.step_form_saved",
    "applications-retry-application-step": "journey.step_retried",
    "applications-acknowledge-application-terms": "journey.terms_accepted",
    "applications-my-identity-manual": "journey.identity_handed_to_staff",
    "applications-take-identity-manual": "journey.identity_taken_by_adviser",
    "applications-submit-my-identity-capture": "journey.identity_photos_submitted",
    # A one-time link so the photos can be taken on a phone, and withdrawing it to take them
    # on this device instead. The photos themselves keep the names above.
    "applications-create-my-identity-phone-link": "journey.identity_phone_link_created",
    "applications-cancel-my-identity-phone-link": "journey.identity_phone_link_withdrawn",
    "applications-fulfil-identity-document": "journey.identity_evidence_uploaded",
    # -- Messaging
    "messaging-messaging-conversations-open": "conversation.opened",
    "messaging-messaging-thread-send-attachment": "message.attachment_sent",
    "messaging-messaging-conversation-send-attachment": "message.attachment_sent",
    "messaging-messaging-thread-send": "message.sent",
    "messaging-messaging-conversation-send": "message.sent",
    "messaging-messaging-group-send-attachment": "message_group.attachment_sent",
    "messaging-messaging-groups-create": "message_group.created",
    "messaging-messaging-group-create-request": "message_group.document_requested",
    "messaging-messaging-group-send": "message_group.message_sent",
    # -- Management reports
    "reports-set-mi-report": "mi_report.switched",
    # -- Notification rules
    "firm-firm-create-notification-rule": "notification_rule.created",
    "admin-create-notification-rule": "notification_rule.created",
    "admin-load-notification-defaults": "notification_rule.defaults_loaded",
    "firm-firm-delete-notification-rule": "notification_rule.deleted",
    "admin-delete-notification-rule": "notification_rule.deleted",
    "firm-firm-update-notification-rule": "notification_rule.updated",
    "admin-update-notification-rule": "notification_rule.updated",
    # -- Notifications
    "notifications-set-notification-client-preferences": "notification.client_preferences_updated",
    "notifications-set-notification-preferences": "notification.preferences_updated",
    "admin-set-notification-config": "notification.settings_updated",
    # -- Provider platforms
    "admin-cancel-platform-run": "platform.run_cancelled",
    "admin-run-platform-action": "platform.action_ran",
    "admin-refresh-platform-tenants": "platform.all_firms_refreshed",
    "admin-set-provider-source": "platform.provider_source_updated",
    "admin-platform-query": "platform.queried",
    "admin-platform-rebuild": "platform.rebuilt",
    "admin-platform-sync": "platform.sync_started",
    # -- Roles
    "firm-create-role": "role.created",
    "admin-create-tenant-role": "role.created",
    "firm-set-role-permissions": "role.permissions_updated",
    "admin-set-tenant-role-permissions": "role.permissions_updated",
    # -- Screening (ComplyCube). Every name keeps the `aml.` prefix:
    # `routers/firm.py::FIRM_EXCLUDED_EVENT_PREFIXES` withholds the family from a firm's own view.
    "aml-run-aml-screen": "aml.screen_ran",
    "aml-decide-aml-match": "aml.match_decided",
    "aml-decide-aml-check": "aml.check_decided",
    "aml-set-aml-monitoring": "aml.monitoring_updated",
    "aml-rerun-aml-check": "aml.check_rerun",
    "aml-request-aml-photos": "aml.photos_requested",
    "aml-upload-aml-identity-photos": "aml.identity_photos_uploaded",
    "aml-allow-aml-journey": "aml.journey_allowed",
    "aml-add-aml-client-note": "aml.client_note_added",
    # Identity images shown by a page that read them ahead: the same act as listing them on the
    # check page (`SYNTHETIC_EVENT_TYPES`, `AML_IMAGES_VIEW_EVENT`).
    "aml-record-aml-check-images-viewed": "aml.identity_images_viewed",
    # FIVE LEGACY NAMES, NO LIVE ROUTE — the Creditsafe console's acts, the same case as
    # `documents-send-document` above. They stay because the UI's `LEGACY_EVENT_TYPES` is generated
    # from this map, and dropping them would leave every row those routes wrote rendering as a raw
    # id.
    "aml-decide-aml-hit": "aml.hit_decided",
    "aml-connect-aml-operator": "aml.operator_connected",
    "aml-disconnect-aml-operator": "aml.operator_disconnected",
    "aml-link-aml-profile": "aml.profile_linked",
    "aml-run-aml-sync": "aml.sync_ran",
    # -- Staff members
    "admin-create-tenant-member": "member.created",
    "firm-deactivate-member": "member.deactivated",
    "admin-deactivate-tenant-member": "member.deactivated",
    "admin-reveal-tenant-member-invitation": "member.invitation_revealed",
    "firm-resend-invite": "member.invite_resent",
    "admin-resend-tenant-member-invite": "member.invite_resent",
    "firm-revoke-invitation": "member.invitation_revoked",
    "admin-revoke-tenant-member-invitation": "member.invitation_revoked",
    "firm-invite-member": "member.invited",
    "admin-reset-tenant-member-mfa": "member.mfa_reset",
    "firm-reset-member-mfa": "member.mfa_reset",
    "admin-revoke-tenant-member-session": "member.session_revoked",
    "firm-revoke-member-session": "member.session_revoked",
    "firm-send-member-password-reset": "member.password_reset_sent",
    "admin-send-tenant-member-password-reset": "member.password_reset_sent",
    "firm-grant-portal": "member.portal_granted",
    "admin-grant-member-portal": "member.portal_granted",
    "firm-revoke-portal": "member.portal_revoked",
    "admin-revoke-member-portal": "member.portal_revoked",
    "firm-set-portal-role": "member.portal_role_updated",
    "admin-set-member-portal-role": "member.portal_role_updated",
    # Which id a custodian knows this person by (`auth.users.provider_refs`). Not a `link.*` event:
    # it is a fact about the MEMBER, and it is the value SEI's enrolment sends as
    # `employeeServicingRelationshipList[].employeeEntityId` — so an operator asking "who told us
    # this adviser is 57817496 at SEI" is asking about the person.
    "admin-set-member-provider-ref": "member.provider_ref_updated",
    # The firm's own door onto the same act — one event type through two doors, which
    # `test_collapsed_acts_are_declared_deliberately` counts on purpose: who did it is on the row
    # (`actor_id`, `target_tenant_id`, `route`) rather than in the name, and that is what stops
    # every subscriber having to know both spellings.
    "firm-set-provider-ref": "member.provider_ref_updated",
    # What a firm SAYS about one of its people: the name, the nature (`type`/`is_adviser`) and the
    # staff profile. A `member.*` event and NOT a `role.*` one, deliberately: this is a statement
    # about the PERSON where a role is a grant — and `eventCategory` files a verb naming a role
    # under Roles, which would put this in the wrong place for anyone scanning for permission
    # changes. One event for the whole save, because it is one editing session on one person.
    "firm-update-member": "member.profile_updated",
    "firm-reactivate-member": "member.reactivated",
    "admin-reactivate-tenant-member": "member.reactivated",
    # -- Storage credentials
    "storage-issue-member-storage-credentials": "storage_credential.issued_for_member",
    "storage-issue-own-storage-credentials": "storage_credential.issued_own",
    "storage-revoke-storage-credentials": "storage_credential.revoked",
    # -- Reviews (four-eye — the decide routes; propose is 202 and is written explicitly)
    "reviews-approve-review": "review.approved",
    "reviews-reject-review": "review.rejected",
    "reviews-cancel-review": "review.cancelled",
    "reviews-set-review-policy": "review.policy_updated",
    # -- Tasks
    "tasks-complete-task": "task.completed",
    "tasks-create-task": "task.created",
    "tasks-update-task": "task.updated",
    # -- Integrations (SEI custodian/software platform approvals and feature toggles —
    # Graphene-ops-only, no tenant scope)
    "integrations-update-integration": "integration.updated",
    # The admin Clients and Accounts tab's one Link action: writes BOTH the contact-level and
    # account-level Intelliflo links in a single call, which is why it does not share either of
    # the platform portal's own two single-write event types above.
    "integrations-link-client-and-account-to-io": "integration.client_account_linked_to_io",
    # One account released from its Intelliflo plan (the plan's policy number blanked first).
    "integrations-unlink-account-from-io": "integration.account_unlinked_from_io",
    # A client released from its Intelliflo client (refused while any of its accounts is mapped).
    "integrations-unlink-client-from-io": "integration.client_unlinked_from_io",
    # The split Clients / Accounts tabs: each Link writes one half of the old combined action.
    "integrations-link-client-to-io": "integration.client_linked_to_io",
    "integrations-link-account-plan": "integration.account_linked_to_io_plan",
    # -- Terms (the versioned legal catalogue)
    #
    # Each authoring act is ONE name through two doors, the email-template pattern: the Graphene
    # route and the firm route call the same service with a different `owner`, so who authored it is
    # on the row (`actor_id`, `target_tenant_id`, `route`) rather than in the name.
    "terms-create-term": "terms.created",
    "terms-create-graphene-term": "terms.created",
    "terms-update-term": "terms.updated",
    "terms-update-graphene-term": "terms.updated",
    # Publication is the act that matters to a compliance reader: a version is immutable once
    # published, and every acceptance from here on pins THAT row.
    "terms-publish-term": "terms.published",
    "terms-publish-graphene-term": "terms.published",
    "terms-archive-term": "terms.archived",
    "terms-archive-graphene-term": "terms.archived",
    "terms-upload-term-file": "terms.file_uploaded",
    # The client's or member's own act, not an authoring one — and the reason the catalogue exists.
    "terms-accept-term": "terms.accepted",
    # -- Transfers
    "transfers-create-transfer": "transfer.created",
    "transfers-set-transfer-status": "transfer.status_updated",
    "transfers-attach-transfer-transaction": "transfer.transaction_attached",
    "transfers-detach-transfer-transaction": "transfer.transaction_detached",
    # The Morgan Lloyd leg. Named after the ACT rather than the vendor call, matching the
    # contribution family above — `cam_createtransfer` is how it is done, not what happened.
    "transfers-record-transfer-to-morgan-lloyd": "transfer.recorded_at_morgan_lloyd",
    "transfers-refresh-transfer-from-morgan-lloyd": "transfer.refreshed_from_morgan_lloyd",
    "transfers-cancel-transfer-at-morgan-lloyd": "transfer.cancelled_at_morgan_lloyd",
    # The custodian legs. Same naming as the Morgan Lloyd family: the act, then where it happened.
    "transfers-record-transfer-to-seccl": "transfer.recorded_at_seccl",
    "transfers-refresh-transfer-from-seccl": "transfer.refreshed_from_seccl",
    "transfers-cancel-transfer-at-seccl": "transfer.cancelled_at_seccl",
    "transfers-record-transfer-to-sei": "transfer.recorded_at_sei",
    "transfers-refresh-transfer-from-sei": "transfer.refreshed_from_sei",
    "transfers-set-transfer-note": "transfer.note_updated",
}


SYNTHETIC_EVENT_TYPES: dict[str, str] = {
    # -- Authentication
    "auth-activated": "authentication.account_activated",
    "auth-login-failed": "authentication.failed",
    "auth-mfa-enrolled": "authentication.mfa_enrolled",
    "auth-mfa-removed": "authentication.mfa_removed",
    "auth-logout": "authentication.signed_out",
    "auth-login": "authentication.succeeded",
    # -- Screening (a GET the middleware never sees: an operator opening a client's identity images)
    "aml-identity-images-viewed": "aml.identity_images_viewed",
    # -- Client identity (a GET: an adviser opening a client's identity images)
    "crm-identity-images-viewed": "client.identity_images_viewed",
    # -- Compliance reports (GETs: a report downloaded, by operations or by a firm)
    "aml-report-downloaded": "aml.report_downloaded",
    "crm-compliance-report-downloaded": "client.compliance_report_downloaded",
    # -- Documents
    "documents-render-document": "document.rendered",
    "documents-view-document": "document.viewed",
    # `shares-access-document-share` (an anonymous public-link access) went with the document
    # share-links feature. Its rows are still in the stream, so the READ side keeps the vocabulary —
    # but nothing can emit it, so it is not declared here.
    # -- Tasks (the worker's notice scan, not a route: `task.created` is the request event)
    "tasks-assigned": "task.assigned",
    "tasks-due-soon": "task.due_soon",
    # -- Reviews (held propose + stale approve — not 2xx on the original door)
    "reviews-requested": "review.requested",
    "reviews-conflicted": "review.conflicted",
}


def event_resource(event_type: str) -> str:
    """The RESOURCE half of an event type: ``document`` for ``document.sent``.

    This is what makes a generic ``{uid}`` path param resolvable — the event says what kind of thing
    its uid names, so nothing has to be inferred from the route. It replaces a lookup table keyed on
    the ROUTER TAG, which said things like "shares means document" that only made sense if you knew
    the file layout.

    A LEGACY name (``action.{tag}-{name}``) has no resource, so its router tag is returned instead —
    which is exactly what the pre-rename tables keyed on, so one map can hold both spellings and a
    reader of history gets the same answer. ``audit.actions`` is append-only, so history is most of
    what these callers read.
    """
    if event_type.startswith(EVENT_PREFIX):
        return event_type[len(EVENT_PREFIX) :].split("-", 1)[0]
    return event_type.split(".", 1)[0]


def event_type_for(route: APIRoute) -> str:
    """The event type this route emits: its declared name in ``EVENT_TYPES``.

    A route with no entry falls back to the old derived shape (``action.{tag}-{name}``) rather
    than raising: an undeclared endpoint should still be recorded, and the tripwire test is what
    makes the omission loud. Never silently emit nothing.
    """
    operation_id = operation_id_for(route)
    declared = EVENT_TYPES.get(operation_id)
    return declared if declared else f"{EVENT_PREFIX}{operation_id}"


async def _write_event(
    *,
    principal: Principal,
    route: APIRoute,
    method: str,
    status: int,
    path_params: dict[str, Any],
    query_params: dict[str, str],
    subject: dict[str, Any] | None = None,
    previous: CapturedPrevious | None = None,
    changed: list[str] | None = None,
    body: dict[str, Any] | None = None,
    changes: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Resolve the actor/tenant context and append the event row.

    Runs as ``service_role`` in its OWN transaction, after the response is sent: ``audit.actions``
    is service_role-only (0041), and the same ``auth.resolve_claims`` the session dependencies use
    is the one source of truth for "which tenant was this principal acting in". The firm an action
    was done TO is resolved to ``target_tenant_id`` so a proxied action records both sides, from
    either of the two ways a route names one: the ``?tenant=<uid>`` query param (the operations
    book/storage/aml convention) or a ``{tenant_id}`` path param (every ops route under
    ``/admin/tenants/{tenant_id}/…``). The query param wins where a route somehow carries both.
    """
    async with db.SessionLocal() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        resolved = (
            await session.execute(
                text("SELECT auth.resolve_claims(:wos_user, :wos_org, :client_id)"),
                {
                    "wos_user": principal.user_id,
                    "wos_org": principal.organisation_id,
                    "client_id": principal.client_id,
                },
            )
        ).scalar_one()
        tenant_id = resolved.get("tenant_id") if resolved else None
        user_uuid = resolved.get("user_uuid") if resolved else None

        target_tenant_id: int | None = None
        target_uid = query_params.get("tenant")
        if target_uid:
            target_tenant_id = (
                await session.execute(
                    text("SELECT id FROM auth.tenants WHERE uid = :uid"), {"uid": target_uid}
                )
            ).scalar_one_or_none()
        elif (path_tenant := path_params.get("tenant_uid")) is not None:
            # A `{tenant_uid}` PATH param — the admin section's own addressing.
            #
            # Resolved rather than trusted, for the same reason the serial branch below is: a row
            # naming a firm that does not exist is a claim we cannot support. A uid that resolves to
            # nothing records NULL and keeps the event.
            target_tenant_id = (
                await session.execute(
                    text("SELECT id FROM auth.tenants WHERE uid = :uid"), {"uid": path_tenant}
                )
            ).scalar_one_or_none()
        elif (path_tenant := path_params.get("tenant_id")) is not None:
            # THE OTHER WAY AN ACTION NAMES A FIRM: a `{tenant_id}` PATH param. Kept because the
            # stream holds rows recorded before the section was uid-addressed, and because a route
            # outside it may still carry a serial.
            #
            # `?tenant=` is the book/storage/aml convention, and it was the only one read here — so
            # the 53 mutating routes under `/admin/tenants/{tenant_id}/…` recorded no target at all.
            # Every one of them is Graphene operations acting ON a firm (its branding, its members,
            # its roles, its providers, its email templates), which is exactly what the target
            # column is for, and the omission had two visible consequences:
            #
            #   * the firm's OWN trail (`GET /firm/audit/actions`, scoped to `t.uid = …` OR
            #     `tt.uid = …`) did not show support actions taken on it — the disclosure it
            #     exists for;
            #   * where the firm being acted on happened to be Graphene's own tenant, the row's only
            #     tenant side was the acting one, so it read as "Your firm" in the platform portal
            #     although it was done from the admin portal.
            #
            # Validated against the registry rather than trusted: the param is a serial id from the
            # URL, and a row naming a tenant that does not exist would be a claim we cannot support.
            # A bad id resolves to NULL — the event still records, because losing the whole event
            # over an unresolvable target is a worse answer than recording it with one side.
            try:
                target_tenant_id = (
                    await session.execute(
                        text("SELECT id FROM auth.tenants WHERE id = :id"),
                        {"id": int(path_tenant)},
                    )
                ).scalar_one_or_none()
            except (TypeError, ValueError):
                target_tenant_id = None

        metadata: dict[str, Any] = {"correlation_id": current_correlation_id()}
        if query_params:
            metadata["query"] = redact(dict(query_params))
        if subject:
            # The allowlisted subject fields (SUBJECT_FIELDS) — already filtered through the
            # redact belt in extract_subject, so they land verbatim.
            metadata["subject"] = subject
        previous_row = pick_previous(
            previous, tenant_id=tenant_id, target_tenant_id=target_tenant_id
        )
        if previous_row:
            # The pre-read before-values (PREVIOUS_FIELDS) — captured before the handler ran,
            # attributed to the resolved tenant only now that resolve_claims has named it.
            metadata["previous"] = previous_row
        if changed:
            # The field NAMES an update carried (CHANGED_KEY_EVENTS). Never their values —
            # see `extract_changed_keys`, which is the whole of the guarantee.
            metadata["changed"] = changed
        if body:
            # The declared, redacted request body (BODY_CAPTURE_EVENTS). This is the widest
            # thing the trail stores — see the note above `BODY_CAPTURE_EVENTS`.
            metadata["body"] = body
        if changes:
            # What the save MOVED: {column: {from, to}} — see the note above
            # `CHANGED_COLUMNS`. The difference between two reads of the row, never the
            # request.
            metadata["changes"] = changes
        if principal.view_as_contact_id is not None:
            # A member of staff acting inside the client portal as one of their clients
            # (app/view_as.py). The actor above is already them rather than the client, so this
            # says the rest of it: that they were in the client's portal at the time, whose, and
            # under which session — enough to pull the whole sitting back out of the trail.
            metadata["view_as"] = {
                "session_uid": principal.view_as_session_uid,
                "contact_id": principal.view_as_contact_id,
            }
        await session.execute(
            text(
                "INSERT INTO audit.actions "
                "  (event_type, tenant_id, target_tenant_id, actor_id, actor_sub, "
                "   method, route, status, path_params, metadata) "
                "VALUES (:event_type, :tenant_id, :target_tenant_id, CAST(:actor_id AS uuid), "
                "  :actor_sub, :method, :route, :status, CAST(:path_params AS jsonb), "
                "  CAST(:metadata AS jsonb))"
            ),
            {
                "event_type": event_type_for(route),
                "tenant_id": tenant_id,
                "target_tenant_id": target_tenant_id,
                "actor_id": str(user_uuid) if user_uuid else None,
                "actor_sub": principal.user_id,
                "method": method,
                "route": route.path,
                "status": status,
                # default=str: path params are str/int by construction, but a custom converter
                # type must degrade to text rather than kill the event.
                "path_params": json.dumps(redact(dict(path_params)), default=str),
                "metadata": json.dumps(metadata, default=str),
            },
        )


# ---- the automation actor ----------------------------------------------------------------------


#: The marker an automation stamps on every event it produces. Read by
#: ``app/flows/automation.py::_automation_marker`` — which is the SELF-TRIGGER GUARD, the single
#: most important safety property in the automation engine — so these three keys are a contract
#: between the executor and the consumer, not a logging convenience:
#:
#:   ``source``      "rule" (a standalone automation) | "flow" (Phase B's action step)
#:   ``id``          the automation's uid
#:   ``chain_depth`` the depth of the firing that produced this event; the consumer adds one
#:
#: ``name`` is additive and display-only (the audit reader has a uid otherwise, which tells a human
#: nothing). The consumer ignores unknown keys, so extras are safe — but never rename or drop the
#: three above without changing ``_automation_marker`` in the same commit: a marker the consumer
#: cannot parse is treated as ABSENT, which silently re-enables automations feeding themselves.
AUTOMATION_MARKER_KEYS = frozenset({"source", "id", "chain_depth"})

#: The synthetic ``actor_sub`` an automation acts under. ``audit.actions.actor_sub`` is NOT NULL
#: because a request always has a verified WorkOS subject; an automation has none, and inventing a
#: plausible-looking human one would be the worst possible answer. This prefix is unmistakable in
#: the trail and greppable in the stream.
AUTOMATION_SUB_PREFIX = "automation:"


def automation_sub(automation_uid: str) -> str:
    return f"{AUTOMATION_SUB_PREFIX}{automation_uid}"


async def record_automation_action(
    session: Any,
    *,
    event_type: str,
    method: str,
    route: str,
    tenant_id: int,
    automation_uid: str,
    automation_name: str,
    chain_depth: int,
    source: str = "rule",
    path_params: dict[str, Any] | None = None,
    subject: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append the action event for work an AUTOMATION did, from worker context.

    ``ActionEventsMiddleware`` can only see requests, and an automation is not one — it runs in the
    worker's automation loop with no ASGI scope, no route and no principal. Without this the entire
    automated half of the product would be invisible in the audit log a firm can already browse
    (``GET /firm/audit/actions``), which would be a worse hole than the one the action stream was
    built to close: automated work is precisely the work nobody watched happen.

    Two deliberate differences from the request-path floor, both improvements it cannot make:

    * **It writes on the CALLER's session**, inside the dispatch savepoint. The request-path
      middleware writes post-commit in its own transaction because it must never break the action
      it observes; here the action and its event are the same unit of work, so a rolled-back
      firing leaves no event claiming it happened. Best-effort was a constraint there, not a
      virtue.
    * **The event names the automation as the actor**, and carries the chain marker the consumer's
      self-trigger guard reads. That marker is what stops an automation's own output firing it
      again — see ``AUTOMATION_MARKER_KEYS``.

    ``event_type``/``method``/``route`` are the ones a HUMAN doing the same act would emit, so a
    reader filtering for "clients tagged" or "tasks raised" sees automated and manual work in one
    list, distinguished by actor rather than hidden in a separate stream.
    """
    metadata: dict[str, Any] = {
        "correlation_id": current_correlation_id(),
        "automation": {
            "source": source,
            "id": automation_uid,
            "chain_depth": chain_depth,
            "name": automation_name,
        },
    }
    if subject:
        # Same discipline as the request path: ids and closed enums only, filtered through the
        # redact belt. The executor's callers pass what the equivalent route's SUBJECT_FIELDS
        # entry would have captured.
        metadata["subject"] = redact(subject)
    if extra:
        metadata.update(redact(extra))

    await session.execute(
        text(
            "INSERT INTO audit.actions "
            "  (event_type, tenant_id, target_tenant_id, actor_id, actor_sub, "
            "   method, route, status, path_params, metadata) "
            "VALUES (:event_type, :tenant_id, NULL, NULL, :actor_sub, "
            "  :method, :route, 200, CAST(:path_params AS jsonb), CAST(:metadata AS jsonb))"
        ),
        {
            "event_type": event_type,
            "tenant_id": tenant_id,
            # No actor_id: there is no auth.users row behind an automation, and pointing at the
            # person who authored the rule would attribute a 3am send to someone who was asleep.
            "actor_sub": automation_sub(automation_uid),
            "method": method,
            "route": route,
            "path_params": json.dumps(redact(dict(path_params or {})), default=str),
            "metadata": json.dumps(metadata, default=str),
        },
    )


# ---- document activity (view / worker render) --------------------------------------------------
# Two kinds of real document activity the request-path middleware cannot see, folded into the
# stream so a firm's audit view (GET /firm/audit/actions) is complete: a document VIEW is a GET
# (the floor records effects, and a read is not a mutation), and a worker RENDER is not a request
# at all. The mutating document routes the middleware DOES cover (send / request / reassign) are
# deliberately NOT re-emitted here — that would double-count them in the stream.
#
# A third writer stood here: ``SHARE_ACCESS_EVENT``/``ANONYMOUS_SHARE_SUB``, for an anonymous hit on
# a public document share link. Both went with the share-links feature, so nothing appends an
# ``action.shares-*`` row any more. The READ side deliberately still knows the vocabulary — historic
# rows must keep humanising (``pkgs/ui/.../audit/describe.ts``, ``app/audit_stream.py``,
# ``routers/admin.py::_UID_KIND_BY_TAG``) — so do not "tidy" those away for symmetry with this.

#: The document-activity event types. Distinct names from the request-path ``document.*`` the
#: middleware emits — a view is not the send, a render is not the generate REQUEST — so a reader
#: filtering the stream never double-counts. Mirrored by the VERBS in
#: ``pkgs/ui/.../audit/describe.ts`` so the headlines read.
#:
#: There was a third, ``SHARE_ACCESS_EVENT`` (an anonymous public-link access). External share links
#: are gone, so the event has no producer — the constant goes with them rather than sitting here
#: describing a route nobody can reach.
DOCUMENT_VIEW_EVENT = "document.viewed"
DOCUMENT_RENDER_EVENT = "document.rendered"

#: The synthetic ``actor_sub`` a worker RENDER acts under. ``actor_id`` still names the staffer who
#: queued the generation (so the trail reads in their name, as ``documents.audit_log`` already
#: does), but the sub records that the WORKER, not a human at a keyboard, performed the render.
WORKER_RENDER_SUB = "system:worker"


async def record_document_action(
    *,
    event_type: str,
    method: str,
    route: str,
    document_uid: str | None = None,
    principal: Principal | None = None,
    tenant_id: int | None = None,
    actor_id: str | None = None,
    actor_sub: str | None = None,
    status: int = 200,
    subject: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    session_maker: Any = None,
) -> None:
    """Append a document-activity event to ``audit.actions`` from a non-request / GET / anonymous
    site the ``ActionEventsMiddleware`` never observes (see the section note above).

    Like the request-path floor and UNLIKE ``record_automation_action``, this runs as
    ``service_role`` in its OWN transaction and is BEST-EFFORT: the view/render has already
    happened, so an audit-stream write must never break it — nor, were it to share the caller's
    transaction, roll it back. ``audit.actions`` is service_role-only (0041), so its own
    transaction is also the only way an authenticated view session — RLS-bound ``authenticated``,
    not ``service_role`` — could write the row at all.

    Actor/tenant context arrives one of two ways. Pass ``principal`` and the same
    ``auth.resolve_claims`` the session dependencies use resolves the tenant + actor uuid — the
    VIEW path, which has a signed-in principal. Or pass ``tenant_id``/``actor_id``/``actor_sub``
    explicitly — the WORKER path, which has none. ``actor_sub`` is NOT NULL in the table, so a
    synthetic marker (``system:worker``) stands in where there is no verified subject.

    ``session_maker`` overrides where the row is written: the worker passes its OWN maker so the
    event lands in the same database its render just committed to (and, in tests, the same
    testcontainer). The view path omits it and uses the app's ``db.SessionLocal`` — which, on
    that request, is the live DB (and the fixture-wired one under test).
    """
    maker = session_maker or db.SessionLocal
    try:
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            resolved_tenant = tenant_id
            resolved_actor = actor_id
            resolved_sub = actor_sub
            if principal is not None:
                claims = (
                    await session.execute(
                        text("SELECT auth.resolve_claims(:wos_user, :wos_org, :client_id)"),
                        {
                            "wos_user": principal.user_id,
                            "wos_org": principal.organisation_id,
                            "client_id": principal.client_id,
                        },
                    )
                ).scalar_one_or_none()
                if claims:
                    if resolved_tenant is None:
                        resolved_tenant = claims.get("tenant_id")
                    if resolved_actor is None:
                        user_uuid = claims.get("user_uuid")
                        resolved_actor = str(user_uuid) if user_uuid else None
                if resolved_sub is None:
                    resolved_sub = principal.user_id

            if resolved_tenant is None or not resolved_sub:
                # Nothing to attribute the event to — skip rather than write a malformed row.
                # Cannot happen on the wired paths (the view path always resolves a principal; the
                # worker path passes a tenant + a synthetic sub); a guard, not a case.
                logger.debug(
                    "record_document_action: no tenant/actor context for %s; skipping", event_type
                )
                return

            metadata: dict[str, Any] = {"correlation_id": current_correlation_id()}
            if subject:
                metadata["subject"] = redact(subject)
            if extra:
                metadata.update(redact(extra))
            path_params = {"uid": document_uid} if document_uid else {}

            await session.execute(
                text(
                    "INSERT INTO audit.actions "
                    "  (event_type, tenant_id, target_tenant_id, actor_id, actor_sub, "
                    "   method, route, status, path_params, metadata) "
                    "VALUES (:event_type, :tenant_id, NULL, CAST(:actor_id AS uuid), :actor_sub, "
                    "  :method, :route, :status, CAST(:path_params AS jsonb), "
                    "  CAST(:metadata AS jsonb))"
                ),
                {
                    "event_type": event_type,
                    "tenant_id": resolved_tenant,
                    "actor_id": resolved_actor,
                    "actor_sub": resolved_sub,
                    "method": method,
                    "route": route,
                    "status": status,
                    "path_params": json.dumps(redact(path_params), default=str),
                    "metadata": json.dumps(metadata, default=str),
                },
            )
    except Exception as exc:  # noqa: BLE001 — the event write must never break the view/render/access
        report_error(
            "document action event write failed",
            exc=exc,
            log=logger,
            event="document_action_event_write_failed",
            event_type=event_type,
        )


# ---- screening activity (an operator viewing identity images) -----------------------------------
# A GET the request-path middleware never sees. Viewing a client's identity document and selfie is
# viewing biometric data, so it is recorded. Written on the CALLER's session, like
# ``record_automation_action``: the AML console's session is already ``service_role``, and the view
# and its record are one unit of work.

#: An operator was shown a client's identity images on a ComplyCube check. ``aml.`` keeps it out of
#: the firm's own audit view (``routers/firm.py::FIRM_EXCLUDED_EVENT_PREFIXES``).
AML_IMAGES_VIEW_EVENT = "aml.identity_images_viewed"
#: A firm's adviser was shown a client's identity images on the client's compliance page.
#: Not ``aml.``: the firm's own view of what its staff did shows it.
CLIENT_IMAGES_VIEW_EVENT = "client.identity_images_viewed"


async def _record_read(
    session: Any,
    *,
    event_type: str,
    principal: Principal,
    tenant_id: int,
    target_tenant_id: int | None,
    actor_id: str | None,
    method: str,
    route: str,
    path_params: dict[str, str],
    subject: dict[str, Any],
) -> None:
    """Append one row for a read the middleware never sees (it records mutating requests only)."""
    metadata: dict[str, Any] = {"correlation_id": current_correlation_id(), "subject": subject}
    await session.execute(
        text(
            "INSERT INTO audit.actions "
            "  (event_type, tenant_id, target_tenant_id, actor_id, actor_sub, "
            "   method, route, status, path_params, metadata) "
            "VALUES (:event_type, :tenant_id, :target_tenant_id, CAST(:actor_id AS uuid), "
            "  :actor_sub, :method, :route, 200, CAST(:path_params AS jsonb), "
            "  CAST(:metadata AS jsonb))"
        ),
        {
            "event_type": event_type,
            "tenant_id": tenant_id,
            "target_tenant_id": target_tenant_id,
            "actor_id": actor_id,
            "actor_sub": principal.user_id,
            "method": method,
            "route": route,
            "path_params": json.dumps(path_params),
            "metadata": json.dumps(metadata, default=str),
        },
    )


async def record_identity_images_view(
    session: Any,
    *,
    principal: Principal,
    tenant_id: int,
    target_tenant_id: int | None,
    actor_id: str | None,
    method: str,
    route: str,
    check_uid: str,
    image_count: int,
    event_type: str = AML_IMAGES_VIEW_EVENT,
    path_params: dict[str, str] | None = None,
) -> None:
    """Append one ``event_type`` row (``aml.identity_images_viewed`` by default) for a check whose
    images were returned. ``path_params`` replace the default ``{"uid": check_uid}``."""
    await _record_read(
        session,
        event_type=event_type,
        principal=principal,
        tenant_id=tenant_id,
        target_tenant_id=target_tenant_id,
        actor_id=actor_id,
        method=method,
        route=route,
        path_params=path_params or {"uid": check_uid},
        subject={"image_count": image_count},
    )


#: Graphene operations downloaded a compliance report in the AML console. ``aml.`` keeps it
#: out of every firm's view.
AML_REPORT_EVENT = "aml.report_downloaded"
#: A firm's adviser downloaded a compliance report on a client's compliance page.
CLIENT_REPORT_EVENT = "client.compliance_report_downloaded"


async def record_report_download(
    session: Any,
    *,
    event_type: str,
    principal: Principal,
    tenant_id: int,
    target_tenant_id: int | None,
    actor_id: str | None,
    route: str,
    path_params: dict[str, str],
    report: str,
) -> None:
    """Append one ``event_type`` row for a downloaded report: ``report`` names which (``client``,
    ``identity`` or ``aml``). An identity report carries the client's photos."""
    await _record_read(
        session,
        event_type=event_type,
        principal=principal,
        tenant_id=tenant_id,
        target_tenant_id=target_tenant_id,
        actor_id=actor_id,
        method="GET",
        route=route,
        path_params=path_params,
        subject={"report": report},
    )


# ---- task notices (the worker's assign / near-due scan) -----------------------------------------
# A THIRD kind of producer, because neither of the two above fits. The task scan
# (``app/tasks/notifier.py``) has no request, so the middleware never sees it; and it is not an
# automation, so it must not stamp ``metadata.automation`` — those three keys are the self-trigger
# guard's contract, and a fabricated automation uid would claim a rule that does not exist.
#
# It writes on the CALLER's session like ``record_automation_action`` and unlike the best-effort
# document writer: ``tasks.tasks.notified_at`` / ``due_soon_notified_at`` is the once-only
# guarantee, so the event and the stamp are one unit of work — a rolled-back scan re-emits, a
# committed one never repeats.

#: A task was assigned to someone / a task assigned to someone is nearly due. The notification
#: catalogue turns each into the notice itself (copy, channels, the firm's own off-switch); nothing
#: here sends.
TASK_ASSIGNED_EVENT = "task.assigned"
TASK_DUE_SOON_EVENT = "task.due_soon"

#: The synthetic ``actor_sub`` a task notice acts under. ``actor_sub`` is NOT NULL and nobody
#: performed this — the scan noticed a state — so a marker says why no person is named. Same
#: ``system:`` convention as ``WORKER_RENDER_SUB``.
TASK_SCAN_SUB = "system:task-scan"


async def record_task_action(
    session: Any,
    *,
    event_type: str,
    tenant_id: int,
    route: str,
    task_uid: str,
    subject: dict[str, Any],
) -> None:
    """Append the action event for a task notice the worker's scan produced.

    ``actor_id`` is NULL and no principal is named: an assignment notice is not an act by the
    person who created the task (it may be minutes or a reassignment later), and a near-due notice
    is the clock's doing. ``subject`` carries what names the task and what
    ``notifications.registry._resolve_task_assignee`` needs to reach its assignee.
    """
    metadata: dict[str, Any] = {
        "correlation_id": current_correlation_id(),
        # Same discipline as every other producer: ids and closed enums, through the redact belt.
        "subject": redact(subject),
    }
    await session.execute(
        text(
            "INSERT INTO audit.actions "
            "  (event_type, tenant_id, target_tenant_id, actor_id, actor_sub, "
            "   method, route, status, path_params, metadata) "
            "VALUES (:event_type, :tenant_id, NULL, NULL, :actor_sub, "
            "  'POST', :route, 200, CAST(:path_params AS jsonb), CAST(:metadata AS jsonb))"
        ),
        {
            "event_type": event_type,
            "tenant_id": tenant_id,
            "actor_sub": TASK_SCAN_SUB,
            "route": route,
            "path_params": json.dumps({"uid": task_uid}),
            "metadata": json.dumps(metadata, default=str),
        },
    )


# ---- auth events (login / logout / failed / activation; MFA-factor events reserved) -------------
# Authentication actions do NOT flow through this API's request surface: login / MFA / logout happen
# in the portals' own SvelteKit servers via WorkOS, and account activation (set-password) lands on
# the token-gated /internal path (exempt, pre-principal). None are 2xx API mutations the middleware
# can observe. So they are RECORDED EXPLICITLY at the seam that performs them, appended here as
# synthetic ``action.auth-*`` events — a closed vocabulary mirrored by
# ``pkgs/ui/.../audit/describe.ts``. Like ``record_document_action`` (and UNLIKE the request floor),
# this is a producer with no tripwire behind it: each new auth seam must call it deliberately.
#
# EMITTED today: activation (from /internal/onboarding-complete), and login / logout / login_failed
# — each portal reports the latter three from its SvelteKit login/logout AND mfa/mfa-enroll flows
# via ``reportAuthEvent`` -> ``POST /internal/auth-event`` (login carries the WorkOS-aligned
# ``method`` — password / mfa). RESERVED (plumbed end to end — constants, endpoint kinds and
# describe.ts labels — but no caller yet): ``mfa_enrolled`` / ``mfa_removed``, the FACTOR-management
# events (enrolling/removing a second factor, distinct from an MFA sign-in, which IS recorded as a
# login). Kept here so the vocabulary is one set.
AUTH_ACTIVATED_EVENT = "authentication.account_activated"
AUTH_LOGIN_EVENT = "authentication.succeeded"
AUTH_LOGIN_FAILED_EVENT = "authentication.failed"
AUTH_LOGOUT_EVENT = "authentication.signed_out"
AUTH_MFA_ENROLLED_EVENT = "authentication.mfa_enrolled"
AUTH_MFA_REMOVED_EVENT = "authentication.mfa_removed"

#: The ``actor_sub`` for a FAILED login — there is no verified WorkOS subject (the credentials were
#: wrong), so a synthetic marker stands in and the attempted email rides the metadata (the
#: security-relevant fact, not a credential). Same convention as ``WORKER_RENDER_SUB``'s
#: ``system:``: a synthetic sub says WHY no person is named, rather than leaving it blank.
ANONYMOUS_LOGIN_SUB = "anonymous:login"


async def record_auth_action(
    *,
    event_type: str,
    tenant_id: int | None,
    actor_sub: str,
    actor_id: str | None = None,
    status: int = 200,
    subject: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    session_maker: Any = None,
) -> None:
    """Append an authentication action event (see the section note). BEST-EFFORT in its own
    ``service_role`` transaction — the auth action has already happened, so the event write must
    never break it. ``actor_id`` (the ``auth.users`` uuid) is resolved from
    ``auth.users.workos_user_id`` (the WorkOS id) when not supplied, so the trail reads in the
    person's name; ``actor_sub`` always stands even when the mirror row cannot be found (a failed
    login has no verified subject at all — it uses ``ANONYMOUS_LOGIN_SUB``). ``status`` mirrors the
    HTTP convention the request floor uses: 200 for a successful auth action, 401 for a failed
    login, so the reader tones it like any other event."""
    maker = session_maker or db.SessionLocal
    try:
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            resolved_actor = actor_id
            if resolved_actor is None:
                # The WorkOS subject is `auth.users.workos_user_id` — the same column
                # `auth.resolve_claims` matches on (0001) — not the legacy `auth_id`.
                sql = "SELECT id FROM auth.users WHERE workos_user_id = :sub AND deleted_at IS NULL"
                params: dict[str, Any] = {"sub": actor_sub}
                if tenant_id is not None:
                    sql += " AND tenant_id = :t"
                    params["t"] = tenant_id
                row = (await session.execute(text(sql), params)).scalar_one_or_none()
                resolved_actor = str(row) if row else None

            metadata: dict[str, Any] = {"correlation_id": current_correlation_id()}
            if subject:
                metadata["subject"] = redact(subject)
            if extra:
                metadata.update(redact(extra))
            # Synthetic method/route (these are not HTTP requests): a readable `/auth/<kind>` pseudo
            # path so the engineer-plumbing line still renders, never an invented real route.
            # `authentication.mfa_enrolled` -> `/auth/mfa-enrolled`. The old shape was
            # `authentication.mfa_enrolled`, so both are handled: a pseudo path is cosmetic, and one
            # that
            # renders as the whole event type would be noise in the plumbing line rather than an
            # error.
            kind = event_type.split(".", 1)[-1].replace("_", "-").removeprefix("auth-")
            await session.execute(
                text(
                    "INSERT INTO audit.actions "
                    "  (event_type, tenant_id, target_tenant_id, actor_id, actor_sub, "
                    "   method, route, status, path_params, metadata) "
                    "VALUES (:event_type, :tenant_id, NULL, CAST(:actor_id AS uuid), :actor_sub, "
                    "  'POST', :route, :status, CAST('{}' AS jsonb), CAST(:metadata AS jsonb))"
                ),
                {
                    "event_type": event_type,
                    "tenant_id": tenant_id,
                    "actor_id": resolved_actor,
                    "actor_sub": actor_sub,
                    "route": f"/auth/{kind}",
                    "status": status,
                    "metadata": json.dumps(metadata, default=str),
                },
            )
            if event_type == AUTH_LOGIN_EVENT and resolved_actor is not None:
                # ``last_sign_in`` existed on the identity mirror from 0001 but no path ever wrote
                # it, so every Team/Profile directory permanently rendered "Never". The portal
                # reports a successful login at the exact seam above; stamp the same event's time
                # here, in the same best-effort transaction.
                await session.execute(
                    text("UPDATE auth.users SET last_sign_in = now() WHERE id = :actor"),
                    {"actor": resolved_actor},
                )
    except Exception as exc:  # noqa: BLE001 — the event write must never break the auth action
        report_error(
            "auth action event write failed",
            exc=exc,
            log=logger,
            event="auth_action_event_write_failed",
            event_type=event_type,
        )


Receive = Any
Send = Any


class ActionEventsMiddleware:
    """Pure ASGI (the ``CorrelationIdMiddleware`` idiom — zero cost on the skip paths, cannot
    swallow streaming responses). Installed innermost in main.py so it sees the ROUTED scope:
    FastAPI's ``APIRoute.matches`` stamps ``scope["route"]``/``scope["path_params"]`` during
    routing, and ``get_current_principal`` stashes the verified principal in ``scope["state"]``
    — both land in the very dict this middleware holds, so by the time ``self.app`` returns the
    attribution is sitting there without a single handler being touched."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in MUTATING_METHODS:
            await self.app(scope, receive, send)
            return

        _lifecycle_event_written.set(False)

        status_seen: dict[str, int] = {}
        # Response subject capture (RESPONSE_SUBJECT_FIELDS): for the few create/upload events whose
        # subject id exists only in the response, TEE the body — every chunk still flows to the
        # client via `send`, we merely copy a bounded prefix to read the id out afterwards. Only for
        # allowlisted event types; everything else pays a single set-membership check per chunk.
        resp_chunks: list[bytes] = []
        resp_overflowed = False

        async def send_and_observe(event: dict[str, Any]) -> None:
            nonlocal resp_overflowed
            if event["type"] == "http.response.start":
                status_seen["status"] = int(event["status"])
            elif event["type"] == "http.response.body" and not resp_overflowed:
                routed = scope.get("route")
                capture = isinstance(routed, APIRoute) and (
                    event_type_for(routed) in RESPONSE_SUBJECT_FIELDS
                )
                if capture:
                    resp_chunks.append(event.get("body", b""))
                    if sum(len(c) for c in resp_chunks) > MAX_RESPONSE_SUBJECT_BYTES:
                        # a body past the cap is not a small created-record JSON — abandon it
                        resp_chunks.clear()
                        resp_overflowed = True
            await send(event)

        # Subject capture: the handler reads the body AFTER routing has stamped scope["route"],
        # so by the time these chunks flow through we know whether this event type has an
        # allowlisted subject at all — anything else passes through unbuffered (bodies are never
        # captured; SUBJECT_FIELDS is the one narrow exception). Overflow drops the buffer for
        # good: a body past the cap is not a subject source (uploads, template payloads).
        #
        # The same hook is the before-value pre-read's timing anchor: FastAPI fully reads the
        # body BEFORE solving dependencies or running the endpoint, so the first body chunk is
        # the last moment the target row still holds its pre-action state. That is why every
        # PREVIOUS_FIELDS route must carry a request body (tripwire-pinned) — a body-less route
        # would never pull `receive` and the pre-read would silently never fire.
        body_chunks: list[bytes] = []
        body_overflowed = False
        previous: CapturedPrevious | None = None
        previous_attempted = False
        row_before: dict[str, Any] | None = None
        row_before_attempted = False

        async def receive_and_buffer() -> dict[str, Any]:
            nonlocal body_overflowed, previous, previous_attempted
            nonlocal row_before, row_before_attempted
            message: dict[str, Any] = await receive()
            if message.get("type") == "http.request":
                routed = scope.get("route")
                if isinstance(routed, APIRoute):
                    event_type = event_type_for(routed)
                    if not previous_attempted and event_type in PREVIOUS_FIELDS:
                        previous_attempted = True
                        previous = await capture_previous(
                            event_type, dict(scope.get("path_params") or {})
                        )
                    # The BEFORE half of the change diff, on the same hook and for the
                    # same reason: this is the last moment the row still holds its
                    # pre-action state.
                    if not row_before_attempted and event_type in CHANGED_COLUMNS:
                        row_before_attempted = True
                        row_before = await read_row(
                            event_type, dict(scope.get("path_params") or {})
                        )
                    # Buffered for EITHER capture: the subject allowlist (values) or the
                    # changed-key list (names). An event on neither is streamed through untouched.
                    if not body_overflowed and (
                        event_type in SUBJECT_FIELDS
                        or event_type in CHANGED_KEY_EVENTS
                        or event_type in BODY_CAPTURE_EVENTS
                    ):
                        body_chunks.append(message.get("body", b""))
                        if sum(len(c) for c in body_chunks) > MAX_SUBJECT_BODY_BYTES:
                            body_chunks.clear()
                            body_overflowed = True
            return message

        # No try/finally on purpose: an exception here means no complete 2xx response was
        # committed to the wire, so there is no effect to record — let it propagate.
        await self.app(scope, receive_and_buffer, send_and_observe)

        status = status_seen.get("status")
        # 202 Accepted is "held for later" (four-eye — docs/reviews.md), not an effect.
        # The happened-event is written only after apply. 202 is 2xx, so it has to be
        # excluded here rather than riding the range check.
        if status is None or not (200 <= status < 300) or status == 202:
            return  # effects only: a refused/failed/held action is not an action taken
        if lifecycle_event_written():
            # The handler already wrote the review-lifecycle row in-transaction
            # (subject includes reviewer_role_slug / proposed_by). Do not emit a
            # second empty-subject event from the decide route.
            return

        route = scope.get("route")
        if not isinstance(route, APIRoute):
            return  # unrouted 2xx (mounts, non-API routes) — outside the guarantee's surface
        if (scope["method"], route.path) in EXEMPT_MUTATING_ROUTES:
            return
        if skips_action_event(route):
            return

        principal = (scope.get("state") or {}).get("principal")
        if not isinstance(principal, Principal):
            # In production this cannot happen on a covered route — the tripwire test forces
            # every non-exempt mutating route through get_current_principal, which stashes the
            # principal. It DOES happen under test dependency_overrides, hence debug, not error.
            logger.debug(
                "no principal stashed for mutating route %s %s — no action event",
                scope["method"],
                route.path,
            )
            return

        try:
            query_params = dict(parse_qsl((scope.get("query_string") or b"").decode("latin-1")))
            event_type = event_type_for(route)
            # Request-supplied subject (SUBJECT_FIELDS) merged with the response-only subject
            # (RESPONSE_SUBJECT_FIELDS, e.g. a create's new id). A given event uses one or the
            # other today, but the union is the honest contract — both are the event's subject.
            body = b"".join(body_chunks)
            subject = extract_subject(event_type, body, query_params) or {}
            response_subject = extract_response_subject(event_type, b"".join(resp_chunks))
            if response_subject:
                subject.update(response_subject)
            changed = extract_changed_keys(event_type, body)
            captured_body = extract_body(event_type, body)
            # The AFTER half. Read here rather than in the handler: the response is already
            # on the wire, so this costs the caller nothing, and reading the COMMITTED row is
            # what makes the diff the outcome rather than the request's intention.
            changes = (
                diff_rows(
                    event_type,
                    row_before,
                    await read_row(event_type, dict(scope.get("path_params") or {})),
                )
                if row_before is not None
                else None
            )
            await _write_event(
                principal=principal,
                route=route,
                method=str(scope["method"]),
                status=status,
                path_params=dict(scope.get("path_params") or {}),
                query_params=query_params,
                subject=subject or None,
                previous=previous,
                changed=changed,
                body=captured_body,
                changes=changes,
            )
        except Exception as exc:  # noqa: BLE001 — the event write must never break the action
            report_error(
                "action event write failed",
                exc=exc,
                log=logger,
                event="action_event_write_failed",
                route=route.path,
                method=str(scope["method"]),
            )
