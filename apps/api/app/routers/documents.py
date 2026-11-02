"""Document management — the storage-backed staff surface (base slice).

Upload a private document (stored in the documents bucket), list/read metadata, and retrieve it
via a proxied stream or a short-lived presigned URL. Runs under ``tenant_session`` (RLS
``authenticated`` enforces tenant isolation); each action is gated by a documents permission.
Contact/client binding, tasks/tags/sharing and the client view land with platform_crm
(see docs/document-management.md).
"""

import hashlib
import json
import logging
import re
from collections.abc import Callable, Coroutine, Sequence
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message, Receive

from app import fatca, listing, live_push, messaging, storage, tenant_storage
from app.addressing import document_type_id
from app.audit_actions import DOCUMENT_VIEW_EVENT, record_document_action
from app.auth import Principal, get_current_principal, require_permission
from app.content import sanitise_html
from app.db import (
    FirmContext,
    book_session,
    portal_session,
)
from app.db import (
    target_tenant_session as tenant_session,
)
from app.document_types import labelize as _labelize
from app.document_types import register_document_type as _register_document_type
from app.document_types import slugify_tag as _slugify_tag
from app.documents import service as docs_service
from app.email import transport as email_mod
from app.flows import form_draft
from app.flows import orchestrator as flows_orchestrator
from app.ids import new_id
from app.permissions import (
    DOCUMENT_TYPES_WRITE,
    DOCUMENTS_READ,
    DOCUMENTS_SEND,
    DOCUMENTS_WRITE,
)
from app.routers.forms import FormFieldOut

logger = logging.getLogger(__name__)


# ── Request-body ceiling (the only layer that sees the wire bytes) ─────────────
# ``storage.read_capped`` bounds what one upload can make us *allocate*, but it runs far too late to
# bound what the request costs: Starlette's multipart parser applies ``max_part_size`` to NON-file
# parts only (``on_part_data`` caps inside ``if part.file is None``) — a file part is spooled into a
# ``SpooledTemporaryFile`` and, past 1 MiB, straight onto the container's temp disk with no ceiling
# at all. FastAPI resolves ``request.form()`` BEFORE the endpoint body (and before its dependencies)
# runs, so no in-endpoint check and no ``Depends`` can pre-empt it. One chunked POST that declares
# no Content-Length therefore used to write an unbounded body to disk before any of our code ran.
#
# So the ceiling is enforced by wrapping the route's ASGI ``receive`` channel: an over-cap declared
# Content-Length is refused outright, and otherwise the running byte count is refused the moment it
# passes the limit, mid-stream. FastAPI's body-reading block re-raises ``HTTPException`` untouched,
# so this surfaces as a plain 413 whether it fires during form parsing or before it.
UPLOAD_BODY_HEADROOM = 1024 * 1024  # multipart boundaries, headers and the small text field parts
# One drop may carry several documents. Bounding the count is what keeps the ceiling below a
# constant instead of "however many parts the client felt like sending" (see upload_document).
MAX_UPLOAD_FILES = 10
MAX_UPLOAD_BODY_BYTES = storage.MAX_DOCUMENT_BYTES * MAX_UPLOAD_FILES + UPLOAD_BODY_HEADROOM
MAX_SINGLE_UPLOAD_BODY_BYTES = storage.MAX_DOCUMENT_BYTES + UPLOAD_BODY_HEADROOM
# Every OTHER route on these routers takes JSON (or no body at all). Their real need is kilobytes —
# a rename, a recipient list, a form's answers — so they must NOT inherit an upload allowance: a
# router-wide multi-file ceiling advertises 251 MiB of pre-auth buffering to a JSON endpoint that
# wants none of it. The upload routes opt into their own, larger ceiling explicitly.
MAX_JSON_BODY_BYTES = 1024 * 1024


def _mib(limit: int) -> str:
    """``limit`` bytes as the size the message should quote. Integer MiB division alone renders any
    sub-MiB limit as "0 MiB", which is only reachable with a patched ceiling today but is just
    kind of nonsense an operator then has to decode from a support ticket."""
    if limit >= 1024 * 1024:
        return f"{limit // (1024 * 1024)} MiB"
    if limit >= 1024:
        return f"{limit // 1024} KiB"
    return f"{limit} bytes"


def body_too_large(limit: int) -> HTTPException:
    """The 413 for a request body over ``limit`` — raised before the body is parsed or stored."""
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"Request body exceeds the {_mib(limit)} limit.",
    )


def capped_receive(receive: Receive, limit: int) -> Receive:
    """``receive`` wrapped so the total ``http.request`` payload can never exceed ``limit`` bytes.

    Raises (rather than truncating) so the caller gets a 413 instead of a mangled body, and so the
    rest of the stream is never read — the point is to stop paying for it."""
    seen = 0

    async def _receive() -> Message:
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body") or b"")
            if seen > limit:
                raise body_too_large(limit)
        return message

    return _receive


class BodyTooLarge(BaseModel):
    """The 413's body — FastAPI's ``{"detail": ...}`` shape, declared so the generated client and
    the portals get a TYPED signal for it. A limit in no route's responses is a limit the UI
    can only discover by guessing at an untyped error."""

    detail: str


_TOO_LARGE_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_413_CONTENT_TOO_LARGE: {
        "model": BodyTooLarge,
        "description": "Request body exceeds this route's ceiling (refused before it is read).",
    }
}


class CappedBodyRoute(APIRoute):
    """A route whose request body is refused past ``body_limit`` bytes, before anything reads it.

    Declares the resulting 413 on itself, so every route that carries the cap also carries the
    contract for breaching it (the cap is set on the ROUTER, so declaring it per handler would mean
    remembering to, and the ones nobody remembered are the ones this class exists for)."""

    body_limit: int = MAX_JSON_BODY_BYTES
    # Methods for which a 413 is a real outcome worth declaring. A GET carries the cap too (the
    # wrapper is per route, not per method) but advertising it on every read would be noise in the
    # generated client for a case no caller can hit.
    _body_methods = frozenset({"POST", "PUT", "PATCH"})

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if self._body_methods & {m.upper() for m in (kwargs.get("methods") or [])}:
            responses = dict(kwargs.get("responses") or {})
            for code, spec in _TOO_LARGE_RESPONSE.items():
                responses.setdefault(code, spec)
            kwargs["responses"] = responses
        super().__init__(*args, **kwargs)

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def capped(request: Request) -> Response:
            limit = self.body_limit
            declared = request.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise body_too_large(limit)
            request._receive = capped_receive(request.receive, limit)
            return await handler(request)

        return capped


class SingleUploadRoute(CappedBodyRoute):
    """One document per request: the ceiling is the per-document cap plus multipart framing."""

    body_limit: int = MAX_SINGLE_UPLOAD_BODY_BYTES


class MultiUploadRoute(CappedBodyRoute):
    """A multi-file drop: ``MAX_UPLOAD_FILES`` documents plus framing. The loosest ceiling we ship,
    so it is opted into by exactly one route rather than inherited router-wide."""

    body_limit: int = MAX_UPLOAD_BODY_BYTES


# The router default is the JSON ceiling; the upload routes are registered imperatively with
# ``route_class_override`` (the decorator form has no way to pass it).
router = APIRouter(prefix="/documents", tags=["documents"], route_class=CappedBodyRoute)

# ── Document types ────────────────────────────────────────────────────────────
# documents.document_type is an OPEN-ENDED tag, not a fixed vocabulary: the per-tenant registry
# documents.document_types (0037) holds the KNOWN tags (the picker suggestions + the auto-classify
# pool), but any new tag used on upload / generate / template-create / reclassify is registered on
# the fly. Normalisation + registration live in app/document_types.py (register_document_type),
# shared with the templates router; the /types + /classify-type endpoints below manage and suggest
# them.


# Shared projection for list/get/upload responses (a document + its file row). Prefer the original
# blob (uploads); fall back to the PDF rendition (generated docs have only pdf_file_id).
_SELECT = (
    "SELECT d.uid, d.name, d.document_type, d.source, d.status::text AS status, "
    "       f.content_type, f.size_bytes, d.created_at, d.generated_at, "
    "       d.error AS error_raw, "
    # Whether the firm may DELETE (archive) this document. False for one it does not own -- a
    # custodian-synced record -- so the UI can withhold the affordance instead of offering a
    # button the API will refuse.
    "       d.deletable, "
    "       tv.version_number AS template_version, tpl.name AS template_name, "
    "       tpl.uid AS template_uid, gb.name AS created_by, "
    # The client's DISPLAY NAME, composed -- not `ic.name`, which is NULLABLE: an individual's
    # name lives in first_name/surname (0002_platform_crm), so a bare `ic.name` was NULL for most
    # clients. The Client column then read '--' for every correctly filed document, and
    # `client-documents.svelte`'s "Add from existing" -- which offers documents with no `client`
    # as UNASSIGNED -- offered them all, letting a filed document be silently re-homed to another
    # client. Same COALESCE the rest of the API composes with (crm.py, contributions.py,
    # transfers.py, matching.py, admin.py); `title` is left out there too, so a document listing
    # reads the same name as every other listing.
    "       COALESCE(NULLIF(TRIM(CONCAT_WS(' ', ic.first_name, ic.surname)), ''), "
    "                ic.name) AS client, "
    # The intended contact's ID as well as their name. The name alone is a display string, and the
    # UI needs the id to carry the client into another surface — "Raise a request" from a document
    # has to open the request builder ON that client, and matching a person by display name is the
    # kind of guess that quietly raises a request against the wrong Margaret.
    "       d.intended_contact_id AS client_contact_id, "
    # ...and their uid, which is what a LINK to the client is built from. The id stays for
    # the request builder, whose `contact_id` is the API's own integer contract.
    "       ic.uid AS client_contact_uid, "
    # Composed for the same reason as `client` above: the 'Shared with' row listed a blank for
    # every individual recipient, and string_agg simply skipped them -- a document sent to three
    # people could report none.
    "       (SELECT string_agg(COALESCE(NULLIF(TRIM(CONCAT_WS(' ', rct.first_name, "
    "                                                    rct.surname)), ''), rct.name), "
    "                          ', ' ORDER BY rdc.added_at) "
    "          FROM documents.document_contacts rdc "
    "          JOIN crm.contacts rct ON rct.id = rdc.contact_id "
    "          WHERE rdc.document_id = d.id AND rdc.role = 'recipient') AS recipients, "
    "       NOT EXISTS (SELECT 1 FROM documents.audit_log al "
    "          WHERE al.document_id = d.id AND al.action = 'document.viewed' "
    "          AND al.actor_id = auth.uid()) AS unread, "
    "       (SELECT MAX(al.created_at) FROM documents.audit_log al "
    "          WHERE al.document_id = d.id AND al.action = 'document.viewed' "
    "          AND al.actor_id = auth.uid()) AS last_viewed_at, "
    # Whether the specific client passed as ``client_contact_id`` (any of their logins, via
    # crm.contact_users) has opened this document — for the staff-side client-detail Documents
    # tab, which asks "has *this* client read it", not "have I (staff) read it" (that's `unread`
    # above). Null/false when no client_contact_id is given (the tenant-wide overview).
    "       (CAST(:client_contact_id AS int) IS NOT NULL AND EXISTS ("
    "          SELECT 1 FROM documents.audit_log al "
    "          JOIN crm.contact_users cu ON cu.user_id = al.actor_id "
    "          WHERE al.document_id = d.id AND al.action = 'document.viewed' "
    "          AND cu.contact_id = CAST(:client_contact_id AS int))) AS client_viewed, "
    "       (SELECT MAX(al.created_at) FROM documents.audit_log al "
    "          JOIN crm.contact_users cu ON cu.user_id = al.actor_id "
    "          WHERE al.document_id = d.id AND al.action = 'document.viewed' "
    "          AND cu.contact_id = CAST(:client_contact_id AS int)) AS client_viewed_at, "
    # Whether this document was actually SENT (delivered) to ``client_contact_id`` — a recipient
    # row (role='recipient') is written on send — as opposed to merely made-for them
    # (``intended_contact_id``). This is what distinguishes a document the client has RECEIVED from
    # one filed under them but never shared. ``sent_at`` is when it was delivered to them. Both
    # null/false on the tenant-wide overview (no client_contact_id).
    "       (CAST(:client_contact_id AS int) IS NOT NULL AND EXISTS ("
    "          SELECT 1 FROM documents.document_contacts sdc "
    "          WHERE sdc.document_id = d.id AND sdc.role = 'recipient' "
    "          AND sdc.contact_id = CAST(:client_contact_id AS int))) AS sent_to_client, "
    "       (SELECT MAX(sdc.added_at) FROM documents.document_contacts sdc "
    "          WHERE sdc.document_id = d.id AND sdc.role = 'recipient' "
    "          AND sdc.contact_id = CAST(:client_contact_id AS int)) AS sent_at "
    "FROM documents.documents d "
    "LEFT JOIN documents.files f ON f.id = COALESCE(d.original_file_id, d.pdf_file_id) "
    "LEFT JOIN documents.template_versions tv ON tv.id = d.template_version_id "
    "LEFT JOIN documents.templates tpl ON tpl.id = tv.template_id "
    "LEFT JOIN auth.users gb ON gb.id = d.generated_by "
    "LEFT JOIN crm.contacts ic ON ic.id = d.intended_contact_id "
)

_INLINE_TYPES = ("application/pdf", "image/png", "image/jpeg")


# ── Render failure surfacing ──────────────────────────────────────────────────
# The worker records a real diagnostic (``f"{type(exc).__name__}: {exc}"``) in
# ``documents.documents.error`` / ``render_jobs.last_error``. That string can quote a dataset's SQL
# or a client's data, so it is NEVER passed through to the API: we map the exception class onto a
# kind + a fixed, safe sentence. Ops read the raw text in the DB (or the worker log).
RenderErrorKind = Literal[
    "empty", "missing_dataset", "template", "data", "renderer", "timeout", "storage", "unknown"
]

_ERROR_KINDS: tuple[tuple[RenderErrorKind, tuple[str, ...]], ...] = (
    # ORDER MATTERS — the first match wins, so the two specific phrases come before the exception
    # families below. Both are worded as whole phrases the worker actually writes, not as exception
    # class names, because neither of these outcomes is an exception at all.
    #
    # `empty` is NOT A FAILURE. The worker records "Dataset X returned no rows" on a document that
    # GENERATED, as an advisory, and without an entry here it fell through to `unknown` — so a
    # perfectly good document reported "Rendering failed unexpectedly — ask support to check the
    # log." to someone whose only real problem was a client with no contributions. That is worse
    # than saying nothing: it sends them to support for an answer the row already had.
    ("empty", ("returned no rows",)),
    # A code the template binds that names no dataset. An authoring fix (re-bind it in the
    # editor), not a data one — the dataset is not unreadable, it does not exist.
    ("missing_dataset", ("no dataset with code", "no longer provides")),
    # Jinja/authoring problems — the fix is in the template editor.
    (
        "template",
        (
            "templatesyntaxerror",
            "templateassertionerror",
            "templatenotfound",
            "templateruntimeerror",
            "templateerror",
            "undefinederror",
            "securityerror",
            "filternotfound",
            "no rendered content",
        ),
    ),
    # Dataset resolution / DB access — the fix is in the dataset or its source.
    (
        "data",
        (
            "programmingerror",
            "dataerror",
            "integrityerror",
            "operationalerror",
            "dbapierror",
            "sqlalchemyerror",
            "datasetunavailable",
        ),
    ),
    ("timeout", ("timeout", "timeouterror", "cancellederror")),
    (
        "storage",
        (
            "storageunavailable",
            "clienterror",
            "endpointconnectionerror",
            "botocoreerror",
            "nocredentialserror",
            "connectionclosederror",
        ),
    ),
    # The renderer itself blew up (WeasyPrint/Chromium, or the browser wouldn't launch).
    (
        "renderer",
        ("weasyprint", "playwright", "browser", "pango", "cairo", "oserror", "processlookuperror"),
    ),
)

#: One SHORT line per kind. These are shown inline on a document row beside a retry control, in a
#: cell that truncates — so a sentence of advice arrived as "Rendering failed for an unexpected
#: reas…" and told the reader nothing at all. Each says what went wrong and, where there is one, the
#: single next step; the retry itself is the control next to it, so the copy never spells out
#: "retry". `kind` is what tests and any future detail panel key on, never this wording.
_ERROR_SUMMARY: dict[RenderErrorKind, str] = {
    # Not "failed": the document is there and correct. It says which way to look — at the client's
    # data, not at the template.
    "empty": "Generated, but some tables had no data for this client.",
    "missing_dataset": "This template uses a dataset that no longer exists — re-bind it.",
    "template": "The template couldn't be rendered — fix it in the editor.",
    "data": "A dataset this template uses couldn't be read.",
    "renderer": "The renderer crashed — try the other renderer.",
    "timeout": "Rendering took too long and was stopped.",
    "storage": "The PDF couldn't be saved.",
    "unknown": "Rendering failed unexpectedly — ask support to check the log.",
}


class RenderError(BaseModel):
    """A failed render, mapped and redacted for display. ``code`` is the exception CLASS NAME only
    (a bare identifier — never the message, which can carry SQL or client data)."""

    kind: RenderErrorKind
    summary: str
    code: str | None = None


def classify_render_error(raw: str | None) -> RenderError | None:
    """Map the worker's raw diagnostic onto a safe ``RenderError``. Pure — unit-tested."""
    if not raw or not raw.strip():
        return None
    code = raw.split(":", 1)[0].strip() or None
    if code and (len(code) > 64 or not code.replace("_", "").isalnum()):
        code = None  # not a bare class name (an unprefixed message) — don't echo it
    haystack = raw.lower()
    for kind, needles in _ERROR_KINDS:
        if any(n in haystack for n in needles):
            return RenderError(kind=kind, summary=_ERROR_SUMMARY[kind], code=code)
    return RenderError(kind="unknown", summary=_ERROR_SUMMARY["unknown"], code=code)


class DocumentOut(BaseModel):
    uid: str
    name: str | None
    document_type: str
    source: str
    status: str
    content_type: str | None
    size_bytes: int | None
    created_at: datetime
    generated_at: datetime | None
    # The template (and its version) that produced a generated document — null for uploads.
    # Version shows as the lists' "Version"; the name powers group-by-template browsing; the uid
    # lets the detail page link back to the exact producing template.
    template_version: int | None = None
    template_name: str | None = None
    template_uid: str | None = None
    # The staff member who generated/uploaded it, the ONE client it's made for
    # (`intended_contact_id`, the "Client" column), and — separately — who it's been delivered to
    # (every `document_contacts` recipient, comma-joined; the "Shared with" row). All null until
    # set. Display-only: `client`/`recipients` don't grant visibility — only a send does.
    created_by: str | None = None
    client: str | None = None
    # …and that client's ID as well as their name. THE NAME ALONE IS NOT ENOUGH and this field being
    # absent was a silent, total failure: `_SELECT` has aliased `d.intended_contact_id AS
    # client_contact_id` since the UI needed it, but pydantic v2 defaults to `extra='ignore'`, so an
    # undeclared kwarg passed to the constructor is DISCARDED — no error, no warning, nothing in
    # `__pydantic_extra__` — and FastAPI's `response_model` then emits declared fields only. The row
    # carried the id, the model dropped it, and the wire never saw it.
    #
    # Two features depended on it and both were dead in every environment: the record's link to the
    # client, and "Raise a request" arriving with that client already chosen. Both gate on
    # `doc.clientContactId != null`, which was always null. The unit tests passed because their
    # fixtures set `client_contact_id` directly — proving the UI works GIVEN the field, and never
    # exercising the path that failed to produce it. `test_documents_api.py` now asserts the
    # endpoint's own response carries it, the only assertion that could have caught this.
    client_contact_id: int | None = None
    # ...and their UID, which is what a LINK to that client is built from (the estate addresses a
    # client by uid; the id above stays because the request builder's `contact_id` is the API's own
    # integer contract). THIS FIELD WENT MISSING THE SAME WAY the comment above describes: `_SELECT`
    # aliases `ic.uid AS client_contact_uid`, the model did not declare it, and pydantic's
    # `extra='ignore'` dropped it silently -- so the record's "Filed for" row rendered as plain
    # text instead of a link, because it gates on `doc.clientContactUid != null`.
    client_contact_uid: str | None = None
    recipients: str | None = None
    # True until the calling principal has opened the document (no `document.viewed` audit event
    # from them yet) — drives the client portal's Unread/Read tag. Per-viewer, not per-document.
    unread: bool
    # When the calling principal last opened the document (their most recent `document.viewed`
    # audit event) — null if they never have. Powers the "Last interacted with" sort option.
    last_viewed_at: datetime | None = None
    # Whether the client passed as `client_contact_id` (list endpoint only) has opened this
    # document, and when — for the staff-facing client-detail Documents tab. False/null when no
    # client_contact_id was given.
    client_viewed: bool = False
    client_viewed_at: datetime | None = None
    # Whether this document has been SENT (delivered) to `client_contact_id`, and when — the
    # client-detail Documents tab's "Sent / Not sent" indication. A document made-for a client
    # (`intended_contact_id`) but not yet sent reads `sent_to_client=false`. False/null when no
    # client_contact_id was given (the tenant-wide overview).
    sent_to_client: bool = False
    sent_at: datetime | None = None
    # Why a `failed` document failed — MAPPED + REDACTED (see classify_render_error), never the
    # worker's raw diagnostic (which can quote a dataset's SQL). `error` is the sentence to show,
    # `error_kind` the machine-readable class to badge/branch on, `error_code` the exception class
    # name (a bare identifier) for support. All null unless a render recorded a failure.
    error: str | None = None
    error_kind: RenderErrorKind | None = None
    error_code: str | None = None
    # False ⇒ archiving is refused (409). Defaults True so every existing caller, and every
    # document written before 0122, reads as deletable; only a writer that owns the document on
    # the firm's behalf (provider ingest) sets it False.
    deletable: bool = True

    @classmethod
    def from_row(cls, row: Any) -> "DocumentOut":
        """Build from a ``_SELECT`` row, mapping the raw ``error`` column onto the safe fields."""
        data = dict(row)
        mapped = classify_render_error(data.pop("error_raw", None))
        return cls(
            **data,
            error=mapped.summary if mapped else None,
            error_kind=mapped.kind if mapped else None,
            error_code=mapped.code if mapped else None,
        )


async def _fetch(
    session: AsyncSession, uid: str, client_contact_id: int | None = None
) -> DocumentOut | None:
    row = (
        (
            await session.execute(
                text(_SELECT + "WHERE d.uid = :uid"),
                {"uid": uid, "client_contact_id": client_contact_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return DocumentOut.from_row(row) if row else None


async def _original_ref(session: AsyncSession, uid: str) -> tuple[str, str, str | None, str] | None:
    """(storage_key, content_type, name, bucket) for the doc's retrievable blob; None if not
    visible.

    Prefers the uploaded original; falls back to the PDF rendition so a generated document (which
    has only ``pdf_file_id``) is downloadable/streamable too.

    The bucket is read from the row, not settings: a tenant's own objects live in its own bucket
    and everything filed earlier lives in the shared one, and the key cannot distinguish them —
    tenant keys carry no tenant segment because the bucket IS the tenant."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT f.storage_key, f.content_type, d.name, f.bucket "
                    "FROM documents.documents d "
                    "JOIN documents.files f ON f.id = COALESCE(d.original_file_id, d.pdf_file_id) "
                    "WHERE d.uid = :uid"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return (row["storage_key"], row["content_type"], row["name"], row["bucket"])


async def _terms_content(session: AsyncSession, uid: str) -> tuple[str | None, str] | None:
    """(name, raw html) of a visible ``terms`` declaration with inline content; None otherwise."""
    sql = (
        "SELECT name, metadata ->> 'content' AS content FROM documents.documents "
        "WHERE uid = :uid AND document_type = 'terms' AND metadata ->> 'content' IS NOT NULL"
    )
    row = (await session.execute(text(sql), {"uid": uid})).mappings().one_or_none()
    return None if row is None else (row["name"], row["content"])


def _content_disposition(disposition: str, name: str) -> str:
    """RFC-6266 ``Content-Disposition`` header value (see ``storage.content_disposition``) — an
    ASCII ``filename`` fallback + UTF-8 ``filename*``, so a non-latin-1 document name (em dash,
    accent) can't crash the header encoder (which 500s → 502 in the portal proxy)."""
    return storage.content_disposition(disposition, name)


async def _audit(
    session: AsyncSession, uid: str, action: str, detail: dict[str, object] | None = None
) -> None:
    """Append a product-level event to documents.audit_log for the document (append-only).

    The implementation moved to ``app/documents/service.py`` when the three client-facing acts were
    extracted for the automation engine; this stays as the request path's ambient-identity spelling
    (no tenant/actor arguments — ``auth.tenant_id()``/``auth.uid()`` answer for the caller)."""
    await docs_service.audit_event(session, uid, action, detail)


async def _assert_visible_contact(session: AsyncSession, contact_id: int) -> None:
    """422 unless ``contact_id`` is a ``crm.contacts`` row the caller may see.

    The client a document/request binds to is the CRM contact (the true client model). This runs
    under ``tenant_session`` (RLS), so the SELECT returns the row only when the caller is entitled
    to it (their assigned book, or the whole firm with ``contacts:read_all``) -- validating both
    that the contact exists in this firm AND that this staffer may target it. RLS on
    ``document_contacts``/``requests`` only checks the tenant, so without this a staffer could bind
    a document/request to a contact id they shouldn't reach."""
    ok = (
        await session.execute(
            text("SELECT 1 FROM crm.contacts WHERE id = :cid"),
            {"cid": contact_id},
        )
    ).scalar_one_or_none()
    if ok is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Unknown client, or you don't have access to them.",
        )


def _reject_oversize(file: UploadFile) -> None:
    """413 if the upload's declared size already exceeds the cap -- a cheap pre-check before we
    touch the body at all. ``_read_upload`` enforces the same cap *while* reading, so a chunked
    upload that declares no Content-Length can't slip past this."""
    if file.size is not None and file.size > storage.MAX_DOCUMENT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Document exceeds the {storage.MAX_DOCUMENT_BYTES // (1024 * 1024)} MiB limit.",
        )


async def _read_upload(file: UploadFile) -> bytes:
    """The upload's bytes, refusing anything over the PER-DOCUMENT cap: the declared size first,
    then the real count as it streams (``storage.read_capped`` aborts at the first over-cap chunk).

    This is the second of two ceilings and bounds only what we copy into memory. The request as a
    whole is bounded earlier, at the ASGI receive channel (``CappedBodyRoute``) — by the time this
    runs, Starlette has already spooled every file part to disk, so nothing here can bound that."""
    _reject_oversize(file)
    try:
        return await storage.read_capped(file)
    except storage.PayloadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Document exceeds the {storage.MAX_DOCUMENT_BYTES // (1024 * 1024)} MiB limit.",
        ) from exc


# Which slice of the library a list call wants. ``active`` is the working set (the default:
# everything live, minus archived); ``archived`` backs the explicit Archived view that makes
# archiving recoverable; ``all`` is every status.
ListStatus = Literal["active", "archived", "failed", "all"]

_LIST_WHERE: dict[ListStatus, str] = {
    "active": "d.status <> 'archived'",
    "archived": "d.status = 'archived'",
    "failed": "d.status = 'failed'",
    "all": "TRUE",
}

# The library's filters, declared the way every other listing declares them (crm.py::_CONTACT_*)
# and applied in Python over the RLS-fetched rows — see app/listing.py for why filtering and
# faceting cannot stay client-side.
#
# THE CLIENT IS FILTERED BY UID, NOT BY NAME. Two clients legitimately share a name, and the whole
# estate addresses a client by uid; filtering on the display string would quietly fold both of them
# into one option. The portal resolves the uid back to a name for the dropdown's label.


def _document_size(row: dict[str, Any]) -> float | None:
    """The document's stored size, or None when it has none.

    `size_bytes` comes off a LEFT JOIN (a queued/failed render has no file yet), and for a RANGE
    filter that missing size is UNKNOWN, not zero — the same call `_contact_value` makes for a
    client with no valued account (crm.py). Reading the coalesced-to-zero column here would answer
    "under 1 MB" for a document that has not rendered at all.
    """
    value = row.get("size_bytes")
    return float(value) if isinstance(value, int | float) else None


_DOCUMENT_FILTERS: listing.Filters = {
    "type": lambda row, value: listing.matches(value, row.get("document_type")),
    "author": lambda row, value: listing.matches(value, row.get("created_by")),
    "client": lambda row, value: listing.matches(value, row.get("client_contact_uid")),
    "size": lambda row, value: listing.within_range(value, _document_size(row)),
    "created": lambda row, value: listing.within_range(value, row.get("created_at")),
}
_DOCUMENT_BOUNDS: dict[str, Callable[[dict[str, Any]], float | None]] = {
    "size": _document_size,
}
_DOCUMENT_FACETS: listing.Facets = [
    ("type", lambda row: row.get("document_type")),
    ("author", lambda row: row.get("created_by")),
    ("client", lambda row: row.get("client_contact_uid")),
]


#: What ``?q=`` matches on a listed row: the document's name and the linked client's (`_SELECT`'s
#: ``client``). One tuple, so the facet labels search exactly what the page does.
_SEARCH_FIELDS = ("name", "client")


def _client_labels(
    rows: list[dict[str, Any]], q: str | None, active: dict[str, str | Sequence[str] | None]
) -> dict[str, str]:
    """Readable names for the client facet, whose VALUES are uids.

    Built over the same set the options are: `facets_for` computes the client options with the
    client filter LIFTED, so naming them from the returned page would resolve only the client
    already filtered to and leave every other option in the dropdown showing a bare nanoid. The
    search still applies (an option is never offered for something the search excluded); every
    other filter does too, matching what `facets_for` does for this facet.
    """
    needle = listing.clean(q)
    return {
        str(row["client_contact_uid"]): str(row["client"] or row["client_contact_uid"])
        for row in rows
        if row.get("client_contact_uid")
        and listing.search(needle, *(row.get(field) for field in _SEARCH_FIELDS))
        and all(
            predicate(row, active.get(key))
            for key, predicate in _DOCUMENT_FILTERS.items()
            if key != "client"
        )
    }


@router.get("", name="list-documents")
async def list_documents(
    contact_id: int | None = None,
    # Named `document_status` locally only to avoid shadowing fastapi's `status` module; the wire
    # contract (and the client) is `?status=`.
    document_status: Annotated[ListStatus, Query(alias="status")] = "active",
    q: str | None = None,
    type: str | None = None,  # noqa: A002 - the wire name; `document_type` is the column
    author: str | None = None,
    client: str | None = None,
    # A PAIR, positionally: `?size=&size=5242880` is "up to 5 MiB" — `listing.clean_range`.
    size: Annotated[list[str] | None, Query()] = None,
    created: Annotated[list[str] | None, Query()] = None,
    limit: int | None = None,
    offset: int = 0,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> list[DocumentOut] | dict[str, object]:
    """List the firm's documents (RLS-scoped), newest first.

    Pass ``contact_id`` to scope to one client's documents — those **made for** that CRM contact
    (``intended_contact_id``) OR **delivered to** them (a ``documents.document_contacts`` recipient)
    — and ``status`` to reach the archived (or failed) slice instead of the working set.

    ``q`` searches the document NAME and the linked CLIENT's name; ``type`` / ``author`` /
    ``client`` are the library's value filters, the last by the client's UID, and ``size`` /
    ``created`` are its two RANGES (bytes, and the upload/generate date). All six narrow the WHOLE
    book before the page is cut, so `total` counts matches rather than the size of the library.

    With ``limit`` the response is the listing envelope (``items`` / ``total`` / ``facets`` /
    ``facet_counts`` / ``bounds``) so the library can page; without it the bare list every existing
    caller already parses.
    """
    where = _LIST_WHERE[document_status]
    # Reused as `client_contact_id` in _SELECT: when scoping to one client's documents, the
    # per-row `client_viewed`/`client_viewed_at` fields ask "has *this* client opened it".
    params: dict[str, object] = {"client_contact_id": contact_id}
    if contact_id is not None:
        # A client's tab shows what's made for them (filed under them at upload/generate, no send
        # required) as well as what's been sent to them — otherwise an upload filed under a client
        # wouldn't appear on their page until it was also delivered. Both arms are now
        # client-visible too (filing binds a 'subject' link), so this staff view and the client's
        # own portal list the same documents; `sent_to_client` is what still distinguishes them.
        where += (
            " AND (d.intended_contact_id = :cid "
            "OR EXISTS (SELECT 1 FROM documents.document_contacts dc "
            "WHERE dc.document_id = d.id AND dc.contact_id = :cid))"
        )
        params["cid"] = contact_id
    order = f"WHERE {where} ORDER BY d.created_at DESC"
    # ONE query, unpaged, then search/filter/facet/cut in Python — the crm.py::list_contacts shape.
    # The SQL LIMIT/OFFSET + separate count(*) it replaces could only page an UNFILTERED book: a
    # count taken in SQL would have reported the size of the library while the page showed matches,
    # and facets computed over one page describe that page rather than the match set.
    rows = (await session.execute(text(_SELECT + order), params)).mappings().all()
    shaped = [dict(r) for r in rows]

    active: dict[str, str | Sequence[str] | None] = {
        "type": listing.clean(type),
        "author": listing.clean(author),
        "client": listing.clean(client),
        "size": listing.clean_range(size),
        "created": listing.clean_range(created),
    }
    # The name and the client it is filed under — what a reader typing a surname into the box
    # expects, since a generated document is named for its kind and not its client. NOT the type:
    # it has its own filter, and folding it in would make a search for "tax" return every tax
    # document alongside the one named for it.
    rows_out, envelope = listing.apply(
        shaped,
        q=q,
        search_fields=_SEARCH_FIELDS,
        filters=_DOCUMENT_FILTERS,
        facets=_DOCUMENT_FACETS,
        active=active,
        limit=limit,
        offset=offset,
        bounds=_DOCUMENT_BOUNDS,
    )
    if envelope is None:
        return [DocumentOut.from_row(r) for r in rows_out]
    return {
        **envelope,
        "items": [DocumentOut.from_row(r) for r in rows_out],
        "facet_labels": {"client": _client_labels(shaped, q, active)},
    }


class UploadRejection(BaseModel):
    """One file in a multi-file upload that could not be stored, and why — so the UI can name it
    instead of reporting a blanket success."""

    filename: str | None
    reason: str


class UploadResult(BaseModel):
    """The outcome of an upload: every document created, plus every file refused. A partly-failed
    drop is a 201 with both lists populated — the accepted files are genuinely stored."""

    documents: list[DocumentOut]
    rejected: list[UploadRejection] = []


async def _store_upload(
    session: AsyncSession,
    *,
    # Typed `int` (was an unused `object`) now that the subject link needs it explicitly: the two
    # INSERTs below still resolve the tenant with `auth.tenant_id()` on this RLS-bound session, so
    # the bucket and the rows are that same tenant's by construction.
    tenant_id: int,
    file: UploadFile,
    data: bytes,
    document_type: str,
    name: str | None,
    intended_contact_id: int | None = None,
    deletable: bool = True,
) -> DocumentOut:
    """Store one validated blob and record its document row (the shared body of the upload loop)."""
    content_type, ext = storage.validate_upload(data, file.content_type)
    doc_uid = new_id()
    # No tenant argument: this path's own INSERT resolves the tenant with `auth.tenant_id()` on
    # this RLS-bound session, so the bucket must be that same tenant's by construction.
    bucket = await tenant_storage.document_bucket(session)
    key = storage.tenant_upload_key(doc_uid, ext)
    try:
        await storage.put_document(key, data, content_type, bucket=bucket)
    except storage.StorageUnavailable as exc:
        # str(exc): the exception distinguishes an unconfigured store from an unreachable one, and
        # sending an operator to check S3_DOCUMENTS_BUCKET when it is set is a wasted afternoon.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    file_id = (
        await session.execute(
            text(
                "INSERT INTO documents.files "
                "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                "VALUES (auth.tenant_id(), :bucket, :key, :ct, :size, :sha) RETURNING id"
            ),
            {
                "bucket": bucket,
                "key": key,
                "ct": content_type,
                "size": len(data),
                "sha": hashlib.sha256(data).hexdigest(),
            },
        )
    ).scalar_one()

    is_pdf = content_type == "application/pdf"
    doc_id = (
        await session.execute(
            text(
                "INSERT INTO documents.documents "
                "  (uid, tenant_id, document_type, source, name, status, "
                "   original_file_id, pdf_file_id, generated_by, intended_contact_id, "
                "   deletable) "
                "VALUES (:uid, auth.tenant_id(), :dtype, 'uploaded', :name, 'generated', "
                "   :fid, :pdf_fid, auth.uid(), :icid, :deletable) RETURNING id"
            ),
            {
                "uid": doc_uid,
                "dtype": document_type,
                "name": name or file.filename,
                "fid": file_id,
                "pdf_fid": file_id if is_pdf else None,
                "icid": intended_contact_id,
                "deletable": deletable,
            },
        )
    ).scalar_one()
    # Filed under a client ⇒ that client can see it, with no separate send
    # (docs_service.link_subject explains why the link is 'subject' and not 'recipient'). The caller
    # validated the contact with _assert_visible_contact, so this cannot file outside their book.
    if intended_contact_id is not None:
        await docs_service.link_subject(
            session,
            tenant_id=tenant_id,
            document_id=doc_id,
            contact_id=intended_contact_id,
        )
    await _audit(
        session,
        doc_uid,
        "document.uploaded",
        {"content_type": content_type, "size_bytes": len(data)},
    )
    doc = await _fetch(session, doc_uid)
    if doc is None:  # pragma: no cover - just inserted under this tenant
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Insert failed"
        )
    return doc


async def upload_document(
    file: list[UploadFile] = File(...),
    document_type: str = Form(...),
    name: str | None = Form(None),
    subject_contact_id: int | None = Form(None),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> UploadResult:
    """Upload one or more private documents: magic-byte sniff + size check, store in the bucket,
    record each. Repeat the ``file`` part per document — a multi-file drop must not lose files, so
    EVERY part is stored and each failure is named back (``rejected``) rather than swallowed.

    The firm classifies the upload (``document_type``) — never the client. Types are open-ended: the
    tag is normalised to a slug and registered in the tenant's registry as a reusable suggestion.
    Each original blob is recorded in ``documents.files`` and linked via ``original_file_id`` (and
    ``pdf_file_id`` when it's a PDF). Status is ``generated`` (the live/active state). ``name``
    renames a single-file upload only; with several files each keeps its own filename.

    ``deletable`` is deliberately NOT a form field. It is a one-way door — nothing sets it back to
    true (no PATCH field, no endpoint), so a caller who could post ``deletable=false`` could mint a
    document that only a hand-written SQL UPDATE can ever remove. Its purpose is provider ingest,
    which does not upload over HTTP: it writes through ``_store_upload``'s keyword instead, where
    the decision belongs to code rather than to whoever can reach this endpoint.

    At most ``MAX_UPLOAD_FILES`` parts per request: the request-body ceiling above is sized for that
    many documents, so accepting more would let one POST cost an unbounded amount. (``file`` itself
    is a required part, so FastAPI 422s an empty request before this handler is reached.)"""
    require_permission(principal, DOCUMENTS_WRITE)
    # Open-ended type: normalise + register the tag (returns the slug), rejecting blank/'auto'.
    document_type = await _register_document_type(session, document_type)
    if len(file) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Upload no more than {MAX_UPLOAD_FILES} files at a time.",
        )

    tenant_id = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one_or_none()
    if tenant_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant context")

    # Filing an upload under a client (subject_contact_id -> intended_contact_id) must be gated on
    # the SAME visibility check the other made-for paths use (generate/request): RLS on
    # documents.documents only scopes by tenant, so without this a staffer outside contacts:read_all
    # could file under ANY client in the firm, and a nonexistent/cross-tenant id would trip the 0035
    # composite FK as a 500 rather than a clean 422. Validate once (every file shares the subject),
    # before storing anything.
    if subject_contact_id is not None:
        await _assert_visible_contact(session, subject_contact_id)

    created: list[DocumentOut] = []
    rejected: list[UploadRejection] = []
    for one in file:
        try:
            data = await _read_upload(one)
            created.append(
                await _store_upload(
                    session,
                    tenant_id=tenant_id,
                    file=one,
                    data=data,
                    document_type=document_type,
                    name=name if len(file) == 1 else None,
                    intended_contact_id=subject_contact_id,
                )
            )
        except ValueError as exc:  # magic-byte / size validation on this one file
            rejected.append(UploadRejection(filename=one.filename, reason=str(exc)))
        except HTTPException as exc:
            # A per-file limit breach is reported per file; anything else (storage down, no tenant)
            # is a whole-request condition and must surface as itself.
            if exc.status_code != status.HTTP_413_CONTENT_TOO_LARGE:
                raise
            rejected.append(UploadRejection(filename=one.filename, reason=str(exc.detail)))
    if not created and rejected:
        # Nothing landed — report it as a rejection of the request, naming every reason.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="; ".join(f"{r.filename or 'file'}: {r.reason}" for r in rejected),
        )
    return UploadResult(documents=created, rejected=rejected)


# Registered imperatively, not with @router.post, purely to give this ONE route the loosest ceiling
# we ship: the router default is the JSON one, and a multi-file drop is the only thing that needs
# more. (Same reason as the client fulfilment upload further down, which needs LESS.)
router.add_api_route(
    "/upload",
    upload_document,
    methods=["POST"],
    name="upload-document",
    status_code=status.HTTP_201_CREATED,
    route_class_override=MultiUploadRoute,
)


class GenerateDocument(BaseModel):
    template_uid: str
    answers: dict[str, Any] = {}
    client_contact_id: int | None = None
    name: str | None = None
    renderer: str | None = None  # override the version's engine ('weasy' | 'chromium')
    # Generate despite unsourced variables (a deliberate incomplete draft). Default off: the
    # completeness gate refuses a template whose tokens have no data source, so a client-facing
    # document can't silently render blank. The 422 names this flag as the override.
    allow_incomplete: bool = False
    # Caller-minted key making a retried/double-submitted generate a no-op: the first call queues,
    # every repeat returns THAT document instead of queueing a second render of the same inputs.
    idempotency_key: str | None = None


@router.post("/generate", name="generate-document", status_code=status.HTTP_201_CREATED)
async def generate_document(
    body: GenerateDocument,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> DocumentOut:
    """Queue a document for generation from a published template + inputs (§8).

    Inserts a ``queued`` document — recording the ``inputs``, the template version and (optionally)
    the client it's for (``intended_contact_id``) — and enqueues a render job, in
    one transaction. The DocumentWorker renders it asynchronously (§10); the caller polls
    ``GET /documents/{uid}`` for ``generated``/``failed``. Returns the document with
    ``status='queued'``.

    A ``client_contact_id`` files it under that client, and a client sees what is filed under them
    (a ``'subject'`` link — ``docs_service.link_subject``). ``POST /documents/{uid}/send`` is still
    the DELIVERY act: the covering note, the notification, and the only thing that reports as sent.

    Pass ``idempotency_key`` to make a double-submit safe: the key is serialised per tenant and the
    already-queued document is returned instead of a second render of the same inputs."""
    require_permission(principal, DOCUMENTS_WRITE)
    tenant_id = await _tenant_id(session)
    # Files the document under this client (the "Client" column on staff's own list) AND lets that
    # client see it — the service binds a 'subject' document_contacts link. Validated HERE rather
    # than in the service because "may this staffer target this client" is a book-scope question
    # (RLS on crm.contacts), not a tenant one — the engine has no book. That check is what stops
    # the filing becoming a disclosure to a client the staffer cannot see.
    if body.client_contact_id is not None:
        await _assert_visible_contact(session, body.client_contact_id)
    try:
        doc_uid = await docs_service.generate(
            session,
            tenant_id=tenant_id,
            template_uid=body.template_uid,
            answers=body.answers,
            name=body.name,
            client_contact_id=body.client_contact_id,
            renderer=body.renderer,
            allow_incomplete=body.allow_incomplete,
            idempotency_key=body.idempotency_key,
        )
    except docs_service.DocumentServiceError as exc:
        # The service's two refusals keep the status codes the route has always returned: an absent
        # template is a 404, an unrenderable/incomplete one a 422.
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if str(exc).startswith("Template not found")
                else status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(exc),
        ) from exc
    doc = await _fetch(session, doc_uid)
    if doc is None:  # pragma: no cover - just inserted under this tenant
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Queue failed"
        )
    return doc


# The most clients one batch may generate for. Generous next to `MAX_UPLOAD_FILES` (10) because
# the cost is not in this request — each client's document is one queued row the DocumentWorker
# drains at its own pace — but bounded all the same: the batch is ONE transaction, so an unbounded
# list holds a pooled connection open for as long as it takes to insert every row, and a firm's
# whole book pasted in by accident should be refused rather than absorbed.
MAX_BATCH_CLIENTS = 250


class GenerateBatch(BaseModel):
    """One template, many clients. `answers` and `name` are SHARED — that is what makes this a
    batch rather than a loop the caller could already write; anything that has to differ per client
    comes from the render context (the client, their adviser, their accounts), not from here."""

    template_uid: str
    contact_ids: list[int]
    answers: dict[str, Any] = {}
    name: str | None = None
    renderer: str | None = None
    allow_incomplete: bool = False
    # Makes the whole batch replayable: each client's document takes `{key}:{contact_id}`, so a
    # retried batch returns the documents the first attempt queued instead of queueing a second
    # render for every client. Without a key a retry doubles the batch, which is exactly the
    # mistake a 250-client action must not make silently.
    idempotency_key: str | None = None


class BatchRejection(BaseModel):
    """One client the batch could not generate for, named so the caller can act on it. A bare count
    leaves them hunting — the same reason the bulk document bar names its untouched rows."""

    contact_id: int
    reason: str


class BatchResult(BaseModel):
    documents: list[DocumentOut]
    rejected: list[BatchRejection]


@router.post(
    "/generate/batch", name="generate-documents-batch", status_code=status.HTTP_201_CREATED
)
async def generate_documents_batch(
    body: GenerateBatch,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> BatchResult:
    """Queue one document per client from a single published template.

    Each client gets an INDEPENDENT document — its own `documents.documents` row, its own render job
    and its own `'subject'` link — exactly as a single generate would produce, not one document
    shared between them. That is the same choice `messaging_groups.create_group_request` makes for a
    group request, and for the same reason: each one is filed, sent, superseded and audited on its
    own.

    PARTIAL SUCCESS IS THE CONTRACT. A client the caller may not target is reported in `rejected`
    and the rest still queue — an operator who picked 24 clients wants the 23 that work plus a
    reason for the one that does not. Nothing landing at all is reported as a refusal of the whole
    request (422, naming every reason), so an empty `documents` is never mistaken for success.

    Conditions of the TEMPLATE — it does not exist, has no renderable content, or has unsourced
    variables — fail the whole request rather than appearing 250 times in `rejected`: they are
    identical for every client, and reporting them per client would bury one fact under a list.

    Duplicate ids collapse: a client named twice gets one document, because the second is the same
    document under the same derived idempotency key, and a batch is a SET of clients.

    KNOWN LIMIT, DELIBERATE — PER-CLIENT AUTOMATIONS DO NOT FIRE. The action stream carries one row
    per REQUEST, so this emits a single `document.batch_generated` (`app/audit_actions.py`)
    rather than one `document.generated` per client. A firm's rule "when a document is generated" is
    action-sourced and keyed on a `contact` subject (`notifications/catalogue.py`,
    `flows/automation.py`), so it sees nothing for a batched client.

    That is left as it stands rather than fixed here, because the fix is a product decision with a
    consequence in both directions: emitting per client would fire every such rule N times from one
    operator click, which for a rule that emails the client means 250 client emails from one button.
    Closing it properly means a per-document DOMAIN event on the flows outbox (the transactional
    path CLAUDE.md points subscribers at) plus a decision about rate — not a loop over
    `record_document_action` here, which would also risk phantom events if this transaction failed
    after the emit.
    """
    require_permission(principal, DOCUMENTS_WRITE)
    tenant_id = await _tenant_id(session)
    if not body.contact_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Choose at least one client to generate for.",
        )
    # De-duped, order preserved: the caller's order is the order the results come back in, which is
    # what lets a UI line the outcomes up against the list it sent.
    contact_ids = list(dict.fromkeys(body.contact_ids))
    if len(contact_ids) > MAX_BATCH_CLIENTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"At most {MAX_BATCH_CLIENTS} clients per batch — "
                f"please generate them in smaller groups."
            ),
        )

    created: list[DocumentOut] = []
    rejected: list[BatchRejection] = []
    for contact_id in contact_ids:
        try:
            # A SELECT-only check, so a refusal here leaves the transaction usable for the clients
            # after it (the reason this is not folded into the savepoint below).
            await _assert_visible_contact(session, contact_id)
        except HTTPException as exc:
            rejected.append(BatchRejection(contact_id=contact_id, reason=str(exc.detail)))
            continue
        try:
            # A SAVEPOINT per client: one client's insert failing (a constraint we did not
            # anticipate) must not abort the transaction and lose the documents already queued for
            # everyone else. Without it the first surprise costs the whole batch.
            async with session.begin_nested():
                doc_uid = await docs_service.generate(
                    session,
                    tenant_id=tenant_id,
                    template_uid=body.template_uid,
                    answers=body.answers,
                    name=body.name,
                    client_contact_id=contact_id,
                    renderer=body.renderer,
                    allow_incomplete=body.allow_incomplete,
                    idempotency_key=(
                        f"{body.idempotency_key}:{contact_id}" if body.idempotency_key else None
                    ),
                )
        except docs_service.DocumentServiceError as exc:
            # A statement about the TEMPLATE, identical for every client — so it fails the request.
            raise HTTPException(
                status_code=(
                    status.HTTP_404_NOT_FOUND
                    if str(exc).startswith("Template not found")
                    else status.HTTP_422_UNPROCESSABLE_CONTENT
                ),
                detail=str(exc),
            ) from exc
        doc = await _fetch(session, doc_uid)
        if doc is None:  # pragma: no cover - just inserted under this tenant
            rejected.append(BatchRejection(contact_id=contact_id, reason="Queue failed"))
            continue
        created.append(doc)

    if not created:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="; ".join(f"client {r.contact_id}: {r.reason}" for r in rejected),
        )
    return BatchResult(documents=created, rejected=rejected)


class ClientOut(BaseModel):
    """A client as a picker shows them. The name PARTS come too, because the journey kickoff
    prefills its own first/middle/surname fields from a chosen client — splitting the display name
    back into parts is the guess ``crm.contacts`` holds three columns to avoid."""

    contact_id: int
    uid: str
    name: str | None
    first_name: str | None = None
    middle_name: str | None = None
    surname: str | None = None
    email: str | None
    type: str
    identity_verified: bool = False
    dob: str | None = None
    retirement_age: int | None = None
    marital_status: str | None = None
    #: The client's own adviser (``crm.client_advisers`` in the ``adviser`` role, a live
    #: ``is_adviser`` login). The journey kickoff reads it to know whether a blank adviser picker
    #: has anyone to inherit; a new client, or one with nobody, has none.
    adviser_id: str | None = None


@router.get("/clients", name="list-document-clients")
async def list_clients(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> list[ClientOut]:
    """The firm's clients (crm.contacts) an adviser can assign document tasks to. RLS-scoped, so a
    staffer sees their assigned book (or the whole firm with contacts:read_all)."""
    require_permission(principal, DOCUMENTS_WRITE)
    rows = (
        (
            await session.execute(
                text(
                    "SELECT c.id AS contact_id, c.uid, c.name, c.first_name, c.middle_name, "
                    "       c.type::text, "
                    "       c.surname, c.email, c.dob::text AS dob, c.retirement_age, "
                    "       c.marital_status, EXISTS ("
                    "         SELECT 1 FROM crm.kyc_checks k "
                    "          WHERE k.tenant_id = c.tenant_id AND k.contact_id = c.id "
                    "            AND k.kind = 'identity' AND k.status = 'passed' "
                    "            AND (k.valid_until IS NULL OR k.valid_until > now())"
                    "       ) AS identity_verified, "
                    "       (SELECT ca.adviser_id::text FROM crm.client_advisers ca "
                    "          JOIN auth.users u ON u.id = ca.adviser_id "
                    "         WHERE ca.tenant_id = c.tenant_id AND ca.contact_id = c.id "
                    "           AND ca.role = 'adviser' AND u.deleted_at IS NULL "
                    "           AND u.is_adviser IS TRUE"
                    "       ) AS adviser_id "
                    "FROM crm.contacts c "
                    "WHERE c.enabled AND c.deleted_at IS NULL "
                    "ORDER BY c.surname NULLS LAST, c.name NULLS LAST, c.id"
                )
            )
        )
        .mappings()
        .all()
    )
    return [ClientOut(**r) for r in rows]


# ── Client notifications ──────────────────────────────────────────────────────


async def _tenant_id(session: AsyncSession) -> int:
    """The caller's tenant (403 without one) — needed to stamp an outbox row + resolve branding."""
    tid = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one_or_none()
    if tid is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant context")
    return int(tid)


async def _actor_name(session: AsyncSession) -> str | None:
    """The calling staffer's display name, for "shared with you by …" copy."""
    return (
        await session.execute(text("SELECT name FROM auth.users WHERE id = auth.uid()"))
    ).scalar_one_or_none()


async def queue_client_email(
    tenant_id: int,
    *,
    event_type: str,
    to: str,
    subject: str,
    html: str,
    sender: email_mod.Sender,
) -> bool:
    """Queue one client email on ``flows.outbox``: the worker's dispatcher delivers ``notify.*``
    rows with backoff, retry and a dead-letter, and the request path does no third-party network IO.

    The implementation moved to ``app/documents/service.py`` (read its docstring for the
    not-transactional caveat and the ``system_transaction`` monkeypatch a test needs); this name
    stays because the document tests monkeypatch it HERE — which is also why the service takes its
    notifier as an argument rather than reaching for a global.
    """
    return await docs_service.queue_client_email(
        tenant_id, event_type=event_type, to=to, subject=subject, html=html, sender=sender
    )


# ── Send (deliver a document to clients) ──────────────────────────────────────
class SendDocument(BaseModel):
    contact_ids: list[int]
    # The adviser's covering note. Persisted on each recipient's `document.sent` audit event (the
    # append-only trail the client's own activity feed reads) and carried in their email.
    message: str | None = None


class SendResult(BaseModel):
    sent: int
    # How many notices were QUEUED for delivery, and the names of the recipients for whom none was —
    # a recipient with no email address gains portal visibility but is told nothing, and the UI must
    # say so rather than promising a notification for everyone. `notified` is deliberately not a
    # delivery count: the outbox row can still retry and dead-letter (a Resend outage, a bounce), so
    # UI copy must say "will be emailed", never "was emailed".
    notified: int = 0
    not_notified: list[str] = []


@router.post("/{uid}/send", name="send-document")
async def send_document(
    uid: str,
    body: SendDocument,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> SendResult:
    """Deliver a document to one or more clients: link each as a recipient so it appears in their
    portal, record the covering note against each send, and email each recipient (queued on the
    notification outbox — the worker delivers it). Interim delivery via ``document_contacts``.

    Gated ``documents:send`` — the client-facing RELEASE act, split out of ``documents:write``
    (drafting): upload/generate/rename/archive stay ``write``, but putting a document in front
    of a client is the moment it leaves the firm."""
    require_permission(principal, DOCUMENTS_SEND)
    tenant_id = await _tenant_id(session)
    try:
        outcome = await docs_service.send(
            session,
            tenant_id=tenant_id,
            document_uid=uid,
            contact_ids=body.contact_ids,
            message=body.message,
            sent_by_name=await _actor_name(session),
        )
    except docs_service.DocumentServiceError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if str(exc) == "Document not found"
                else status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            detail=str(exc),
        ) from exc
    return SendResult(
        sent=outcome.sent, notified=outcome.notified, not_notified=outcome.not_notified
    )


# ── Notify (tell a document's clients about it — no visibility change, no delivery) ──
# NOTIFY IS GONE. `POST /documents/{uid}/notify` told a client that a document filed under them was
# there. Removed rather than kept: a DOCUMENT REQUEST already asks a client to act on a document,
# with a due date, a line item per action and a fulfilment trail — everything a notice had, plus a
# record of whether the client did anything. Two features for one job, and the weaker one reported
# "notified" without being able to say anything had been read.
#
# What stays: `documents:send` still gates the flow-engine actions that send and request documents
# (`flows/effects.py`), and the event vocabulary keeps `document.sent` / `document.notified` as
# LEGACY names — `audit.actions` is append-only, so the rows both routes already wrote must still
# humanise (see `actions.EVENT_TYPES`).


_FILE_ACTIONS = ("upload",)
# The audit-log verb each fulfilment records, so the activity feed reflects what actually happened.
# File actions create a document; confirm actions (agree/acknowledge) act on the target document.
_FULFIL_EVENT = {
    "upload": "document.uploaded",
    "complete_form": "document.completed",
    "agree": "document.agreed",
    "acknowledge": "document.acknowledged",
}

# The chat-echo verb each fulfilment posts as a client message (fulfill_request_item) — a plain
# past-tense label, not the audit-log event name above (that vocabulary is internal).
_FULFIL_CHAT_VERB = {
    "upload": "Uploaded",
    "agree": "Agreed to",
    "acknowledge": "Acknowledged",
}


class RequestItemIn(BaseModel):
    # 'agree' is confirm-only for now (view + tick "I agree"); real signing comes later.
    action: Literal["upload", "agree", "acknowledge", "complete_form"]
    title: str
    category: str | None = None
    format_hint: str | None = None
    target_document_uid: str | None = None
    # For complete_form: the builder form the client fills in-app (its stable uid).
    form_uid: str | None = None


class CreateRequest(BaseModel):
    contact_id: int
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    message: str | None = None
    due_date: str | None = None
    items: list[RequestItemIn]


class UpdateRequest(BaseModel):
    # Only the due date is editable for now; None clears it.
    due_date: str | None = None


class FormResponseIn(BaseModel):
    # The client's answers, keyed by fieldKey (DynamicForm.collect() output).
    answers: dict[str, Any]


class RequestItemOut(BaseModel):
    uid: str
    action: str
    title: str
    category: str | None
    format_hint: str | None
    form_uid: str | None
    target_document_uid: str | None
    fulfilled_document_uid: str | None
    status: str


class RequestOut(BaseModel):
    uid: str
    contact_id: int
    #: The client's own uid — what a portal builds a link to their record from. `contact_id` is
    #: the join key the API filters on and is never an address.
    contact_uid: str | None
    contact_name: str | None
    priority: str
    message: str | None
    due_date: date | None
    status: str
    created_at: datetime
    items: list[RequestItemOut]


#: A client's readable name. `crm.contacts.name` is NULLABLE — an individual's name lives in
#: `first_name`/`surname` — so reading the bare column shows nothing for every individual, which is
#: the trap that made the documents Client column render "—" across the whole library. Declared
#: once because the projection and the SEARCH both need it: composing it in one and not the other
#: gives a page whose search cannot find the names it is displaying.
_CONTACT_NAME = "COALESCE(NULLIF(TRIM(CONCAT_WS(' ', c.first_name, c.surname)), ''), c.name)"

# `_CONTACT_NAME` is a module constant, not a caller string — nothing here is interpolated from
# a request.
_REQ_HEADER = (
    "SELECT r.id, r.uid, r.contact_id, c.uid AS contact_uid, "  # noqa: S608
    f"       {_CONTACT_NAME} AS contact_name, r.priority, "
    "       r.message, r.due_date, r.status, r.created_at "
    "FROM documents.requests r LEFT JOIN crm.contacts c ON c.id = r.contact_id "
)
_REQ_ITEMS = (
    "SELECT i.request_id, i.uid, i.action, i.title, i.category, i.format_hint, i.form_uid, "
    "       td.uid AS target_document_uid, fd.uid AS fulfilled_document_uid, i.status "
    "FROM documents.request_items i "
    "LEFT JOIN documents.documents td ON td.id = i.target_document_id "
    "LEFT JOIN documents.documents fd ON fd.id = i.fulfilled_document_id "
    "WHERE i.request_id = ANY(:ids) ORDER BY i.id"
)


async def _build_requests(
    session: AsyncSession, headers: list[dict[str, object]]
) -> list[RequestOut]:
    """Attach each request's line items (one extra query, RLS-scoped) and shape the response."""
    ids = [h["id"] for h in headers]
    items_by_req: dict[object, list[RequestItemOut]] = {}
    if ids:
        rows = (await session.execute(text(_REQ_ITEMS), {"ids": ids})).mappings().all()
        for row in rows:
            data = dict(row)
            items_by_req.setdefault(data.pop("request_id"), []).append(RequestItemOut(**data))
    out = []
    for h in headers:
        data = dict(h)
        out.append(RequestOut(**data, items=items_by_req.get(data.pop("id"), [])))
    return out


async def _request_fetch(session: AsyncSession, uid: str) -> RequestOut | None:
    headers = (
        (await session.execute(text(_REQ_HEADER + "WHERE r.uid = :uid"), {"uid": uid}))
        .mappings()
        .all()
    )
    reqs = await _build_requests(session, [dict(h) for h in headers])
    return reqs[0] if reqs else None


#: The statuses a request can be in. An allowlist, so a typo is a 422 naming the mistake rather
#: than an empty list — which is indistinguishable from "this firm has no outstanding requests".
_REQUEST_STATUSES = ("pending", "fulfilled", "cancelled")


@router.get("/requests", name="list-document-requests")
async def list_requests(
    contact_id: int | None = None,
    # `Annotated[..] = None`, not `Query(default=None)`: the latter leaves the Query object
    # itself as the default when this function is called directly rather than through HTTP —
    # which every test does — so the guard below saw a sentinel instead of None and 422'd on a
    # call that named no status at all.
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    adviser: str | None = None,
    q: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    session: AsyncSession = Depends(book_session),
) -> list[RequestOut] | dict[str, Any]:
    """Requests (RLS: staff see the tenant's; a client sees their own).

    ``contact_id``, when given, narrows to that ONE client's requests — the active-contact
    selector for a client-portal login linked to more than one contact. ANDed on top of whatever
    RLS already allows, so an id outside the caller's real scope narrows to nothing rather than
    widening access.

    ``adviser=me`` narrows to requests for clients on the CALLER'S OWN BOOK
    (``crm.client_advisers``), which is what the dashboard's "Action required" queue asks for: it
    is the surface that answers "what needs ME", and a firm's whole outstanding list is not an
    answer to that question. It is the mirror of ``GET /tasks?assignee=me`` and, like it, only ever
    NARROWS — the requests PAGE stays the firm-wide queue it is meant to be. Only ``me`` is
    accepted; anything else is a 422, because a filter nobody can read silently returning
    everything is how a scoped surface stops being trusted.

    **Two response shapes, chosen by ``limit``**, which is the convention ``/crm/accounts`` and
    ``/crm/holdings`` already set: absent ⇒ the bare list every existing caller reads (the client
    portal, the client record's message rail), so this stays a widening rather than a break; given
    ⇒ ``{items, total, counts}``.

    ``counts`` is what makes paging possible at all here. The requests page is three tabs labelled
    with their own sizes — "Outstanding (12)" — and it counted the loaded array. Send it a window
    and every tab would report the size of the window instead of the size of the book, which is a
    worse answer than a slow page: it is a specific wrong number, stated confidently. The counts
    are computed over the whole filtered set, ignoring ``status`` (each tab needs the others'
    totals) but honouring ``contact_id`` and ``q``.
    """
    where = ["(CAST(:cid AS int) IS NULL OR r.contact_id = CAST(:cid AS int))"]
    params: dict[str, Any] = {"cid": contact_id}
    if adviser is not None:
        if adviser != "me":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="adviser must be 'me'",
            )
        # EXISTS rather than a join: a client with several staff on their team (any role, 0245)
        # must not double their requests.
        where.append(
            "EXISTS (SELECT 1 FROM crm.client_advisers ca "
            "WHERE ca.contact_id = r.contact_id AND ca.adviser_id = auth.uid())"
        )
    if q and q.strip():
        # The client's name and the request's own message — what a person searches a queue by.
        # The COMPOSED name, so searching for an individual by their name actually finds them.
        where.append(f"({_CONTACT_NAME} ILIKE :q OR r.message ILIKE :q)")
        params["q"] = f"%{q.strip()}%"
    base = " WHERE " + " AND ".join(where)

    if limit is None:
        rows = (
            (
                await session.execute(
                    text(_REQ_HEADER + base + " ORDER BY r.created_at DESC"), params
                )
            )
            .mappings()
            .all()
        )
        return await _build_requests(session, [dict(r) for r in rows])

    if status_filter is not None and status_filter not in _REQUEST_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"status must be one of {', '.join(_REQUEST_STATUSES)}",
        )

    # Outstanding AND past its date — the page's one headline number. Counted here for the same
    # reason the tab sizes are: derived from the loaded rows it would only ever describe the
    # window, and "3 overdue" is a specific claim about the whole queue.
    overdue = int(
        (
            await session.execute(
                # `base` is fixed text plus bound parameters.
                text(
                    "SELECT count(*) FROM documents.requests r "  # noqa: S608
                    "LEFT JOIN crm.contacts c ON c.id = r.contact_id"
                    + base
                    + " AND r.status = 'pending' AND r.due_date IS NOT NULL "
                    "AND r.due_date < CURRENT_DATE"
                ),
                params,
            )
        ).scalar_one()
    )

    # Every tab's size, in one pass, BEFORE the window and before the status narrowing.
    counts = {
        str(row["status"]): int(row["n"])
        for row in (
            await session.execute(
                # `base` is fixed text plus bound parameters — no caller string reaches the SQL.
                text(
                    "SELECT r.status, count(*) AS n FROM documents.requests r "  # noqa: S608
                    "LEFT JOIN crm.contacts c ON c.id = r.contact_id" + base + " GROUP BY r.status"
                ),
                params,
            )
        )
        .mappings()
        .all()
    }

    scoped = base
    if status_filter is not None:
        scoped += " AND r.status = :st"
        params["st"] = status_filter
    params["limit"] = max(1, min(limit, 200))
    params["offset"] = max(0, offset)
    rows = (
        (
            await session.execute(
                text(  # noqa: S608 - fixed text plus bound parameters
                    _REQ_HEADER
                    + scoped
                    # `id` breaks the tie: two requests raised in the same second can otherwise
                    # swap between windows, and one of them is never seen at all.
                    + " ORDER BY r.created_at DESC, r.id DESC LIMIT :limit OFFSET :offset"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    items = await _build_requests(session, [dict(r) for r in rows])
    return {
        "items": items,
        # The size of what the caller is paging through — the filtered set, narrowed by status.
        "total": sum(counts.values()) if status_filter is None else counts.get(status_filter, 0),
        "counts": {s: counts.get(s, 0) for s in _REQUEST_STATUSES},
        "overdue": overdue,
    }


@router.post("/requests", name="create-document-request", status_code=status.HTTP_201_CREATED)
async def create_request(
    body: CreateRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> RequestOut:
    """Adviser asks a client to do one or more things (upload/sign/acknowledge/complete a form).

    Gated ``documents:send`` (not ``write``): a request lands in the client's portal and email,
    so raising one is the same client-facing release act as sending a document."""
    require_permission(principal, DOCUMENTS_SEND)
    # Tenant first, then book scope, then the service. The service takes the tenant explicitly
    # (the engine calls it too and has no ambient identity), so resolving it is the first thing
    # this route can do; `_assert_visible_contact` then adds the question the engine has no
    # equivalent of — may THIS staffer reach this client (their book, via RLS)?
    tenant_id = await _tenant_id(session)
    await _assert_visible_contact(session, body.contact_id)
    try:
        req_uid = await docs_service.create_request(
            session,
            tenant_id=tenant_id,
            contact_id=body.contact_id,
            items=[
                docs_service.RequestItemSpec(
                    action=i.action,
                    title=i.title,
                    category=i.category,
                    format_hint=i.format_hint,
                    target_document_uid=i.target_document_uid,
                    form_uid=i.form_uid,
                )
                for i in body.items
            ],
            priority=body.priority,
            message=body.message,
            due_date=body.due_date,
        )
    except docs_service.DocumentServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    req = await _request_fetch(session, req_uid)
    if req is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Create failed"
        )
    return req


@router.post("/requests/{req_uid}/cancel", name="cancel-document-request")
async def cancel_request(
    req_uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> RequestOut:
    """Cancel a pending request (and its still-pending items)."""
    require_permission(principal, DOCUMENTS_WRITE)
    row = (
        await session.execute(
            text(
                "UPDATE documents.requests SET status = 'cancelled', completed_at = now() "
                "WHERE uid = :uid AND status = 'pending' RETURNING id"
            ),
            {"uid": req_uid},
        )
    ).first()
    if row is not None:
        await session.execute(
            text(
                "UPDATE documents.request_items SET status = 'cancelled' "
                "WHERE request_id = :rid AND status = 'pending'"
            ),
            {"rid": row[0]},
        )
    req = await _request_fetch(session, req_uid)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return req


@router.patch("/requests/{req_uid}", name="update-document-request")
async def update_request(
    req_uid: str,
    body: UpdateRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> RequestOut:
    """Update a pending request's due date (staff only; RLS requests_write scopes it to the tenant).
    Fulfilled/cancelled requests are locked, mirroring the cancel endpoint's pending-only guard."""
    require_permission(principal, DOCUMENTS_WRITE)
    row = (
        await session.execute(
            text(
                "UPDATE documents.requests SET due_date = CAST(:due AS date) "
                "WHERE uid = :uid AND status = 'pending' RETURNING id"
            ),
            {"uid": req_uid, "due": body.due_date},
        )
    ).first()
    if row is None:
        # Distinguish "no such request" (404) from "exists but not editable" (409).
        exists = (
            await session.execute(
                text("SELECT 1 FROM documents.requests WHERE uid = :uid"), {"uid": req_uid}
            )
        ).first()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT if exists else status.HTTP_404_NOT_FOUND,
            detail="Only pending requests can be edited." if exists else "Request not found",
        )
    req = await _request_fetch(session, req_uid)
    if req is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Update failed"
        )
    return req


async def store_request_upload(
    session: AsyncSession,
    *,
    item: Any,
    item_uid: str,
    file: UploadFile,
) -> tuple[object, str]:
    """Store one upload-backed request item for either the client or an acting adviser.

    Authorisation and the final item transition stay with the caller. Keeping the storage,
    document, contact-link and audit write here means the two doors produce exactly the same
    evidence record.

    The item's category is the document's type, and a category in ``_REQUEST_UPLOAD_ACCEPT``
    narrows what the store takes. A ``fatca_form`` upload is the client's signed W-9 or W-8, so it
    is also linked to their answer (``fatca.link_signed_form``) in the same transaction.
    """
    document_type = _slugify_tag(item["category"] or "") or "other"
    doc_id, doc_uid = await store_contact_upload(
        session,
        tenant_id=int(item["tenant_id"]),
        contact_id=int(item["contact_id"]),
        file=file,
        document_type=document_type,
        accept=_REQUEST_UPLOAD_ACCEPT.get(document_type),
        audit_action=_FULFIL_EVENT[str(item["action"])],
        audit_detail={"request_item": item_uid},
    )
    if document_type == fatca.FORM_DOCUMENT_TYPE:
        await fatca.link_signed_form(
            session,
            tenant_id=int(item["tenant_id"]),
            contact_id=int(item["contact_id"]),
            document_id=doc_id,
        )
    return doc_id, doc_uid


def signed_copy_only(content_type: str) -> str | None:
    """``store_contact_upload``'s ``accept`` for a signed form: a scan or a photograph, so a PDF
    or an image. The document store's own allowlist also takes Office files and plain text, and a
    ``.txt`` reading "they signed" is exactly the assertion a signed copy exists to replace."""
    if content_type == "application/pdf" or content_type.startswith("image/"):
        return None
    return "The signed copy needs to be a PDF or an image (a scan or a photograph)."


#: Request-item categories whose upload has a shape of its own: ``store_request_upload`` passes
#: the rule as ``store_contact_upload``'s ``accept``. A signed W-9 / W-8 is a signed copy.
_REQUEST_UPLOAD_ACCEPT: dict[str, Callable[[str], str | None]] = {
    fatca.FORM_DOCUMENT_TYPE: signed_copy_only,
}


async def store_contact_upload(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    file: UploadFile,
    document_type: str,
    audit_action: str,
    audit_detail: dict[str, object] | None = None,
    accept: Callable[[str], str | None] | None = None,
    name: str | None = None,
) -> tuple[object, str]:
    """File one uploaded piece of evidence on a client's record.

    The storage write, the ``documents.files`` + ``documents.documents`` rows, the owner link to
    the contact and the audit entry — everything that makes an upload a document the client
    record can show. Shared by the request-item fulfilment paths, the in-person terms acceptance
    (which files the signed copy, an upload no request item asked for) and the client's W-9 / W-8,
    so every evidence file lands with the same shape whichever door it came through.

    ``accept`` narrows the store's own allowlist for a caller whose evidence has a shape — a
    signed copy is a scan or a photograph (``signed_copy_only``). It is given the SNIFFED content
    type (never the declared one) and returns the refusal to show, or ``None`` to let the file
    through. ``audit_detail`` is merged under the content type and size the entry always carries.
    ``name`` is the document's name; without one it is the uploaded file's name.
    """
    data = await _read_upload(file)
    try:
        content_type, ext = storage.validate_upload(data, file.content_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    refusal = accept(content_type) if accept is not None else None
    if refusal:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=refusal)
    doc_uid = new_id()
    bucket = await tenant_storage.document_bucket(session, tenant_id)
    key = storage.tenant_upload_key(doc_uid, ext)
    try:
        await storage.put_document(key, data, content_type, bucket=bucket)
    except storage.StorageUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    file_id = (
        await session.execute(
            text(
                "INSERT INTO documents.files "
                "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                "VALUES (:t, :bucket, :key, :ct, :size, :sha) RETURNING id"
            ),
            {
                "t": tenant_id,
                "bucket": bucket,
                "key": key,
                "ct": content_type,
                "size": len(data),
                "sha": hashlib.sha256(data).hexdigest(),
            },
        )
    ).scalar_one()
    is_pdf = content_type == "application/pdf"
    doc_id = (
        await session.execute(
            text(
                "INSERT INTO documents.documents "
                "  (uid, tenant_id, document_type, source, name, status, "
                "   original_file_id, pdf_file_id, generated_by) "
                "VALUES (:uid, :t, :dtype, 'uploaded', :name, 'generated', "
                "  :fid, :pdf, auth.uid()) RETURNING id"
            ),
            {
                "uid": doc_uid,
                "t": tenant_id,
                "dtype": document_type,
                "name": name or file.filename or "Upload",
                "fid": file_id,
                "pdf": file_id if is_pdf else None,
            },
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO documents.document_contacts "
            "  (document_id, tenant_id, contact_id, role, added_by) "
            "VALUES (:doc, :t, :cid, 'owner', auth.uid()) "
            "ON CONFLICT (document_id, contact_id, role) DO NOTHING"
        ),
        {"doc": doc_id, "t": tenant_id, "cid": contact_id},
    )
    # The tenant is the caller's resolved one, not `auth.tenant_id()`: under `firm_session` the
    # session runs as service_role and the claim may be absent (the in-person terms route hit
    # exactly that, and the insert wrote NULL). The actor stays ambient — `auth.uid()` names the
    # person at the keyboard, and there always is one on this path.
    await docs_service.audit_event(
        session,
        doc_uid,
        audit_action,
        {"content_type": content_type, "size_bytes": len(data), **(audit_detail or {})},
        tenant_id=tenant_id,
    )
    return doc_id, doc_uid


async def fulfill_request_item(
    req_uid: str,
    item_uid: str,
    file: UploadFile | None = File(None),
    ctx: FirmContext = Depends(portal_session),
) -> RequestOut:
    """A client fulfils one item of their own request. Runs elevated (service_role via
    firm_session): the parent request must belong to the caller (user_id = auth.uid()) and the item
    be pending — that ownership check is the entire authorisation boundary, since service_role
    bypasses RLS. The file-backed action (upload) requires a file and creates a document;
    agree/acknowledge are confirm-only; complete_form is rejected here (use the form-response
    endpoint)."""
    session = ctx.session
    item = (
        (
            await session.execute(
                text(
                    "SELECT i.id, i.request_id, i.tenant_id, i.action, i.title, i.category, "
                    "       i.target_document_id, td.uid AS target_uid, r.contact_id "
                    "FROM documents.request_items i "
                    "JOIN documents.requests r ON r.id = i.request_id "
                    "LEFT JOIN documents.documents td ON td.id = i.target_document_id "
                    "WHERE i.uid = :iuid AND r.uid = :ruid "
                    "  AND r.contact_id = ANY(auth.contact_scope()) AND i.status = 'pending'"
                ),
                {"iuid": item_uid, "ruid": req_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request item not found")
    if item["action"] == "complete_form":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Complete a form via the form-response endpoint, not fulfil.",
        )

    tenant_id = int(item["tenant_id"])
    doc_id: object | None = None

    if item["action"] in _FILE_ACTIONS:
        if file is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="This action requires a file upload.",
            )
        doc_id, _ = await store_request_upload(session, item=item, item_uid=item_uid, file=file)
    elif item["target_document_id"] is not None:
        # Confirm-only (agree / acknowledge): make sure the client can still see the document they
        # acted on, and record the action against it.
        await session.execute(
            text(
                "INSERT INTO documents.document_contacts "
                "  (document_id, tenant_id, contact_id, role, added_by) "
                "VALUES (:doc, :t, :cid, 'recipient', auth.uid()) "
                "ON CONFLICT (document_id, contact_id, role) DO NOTHING"
            ),
            {"doc": item["target_document_id"], "t": tenant_id, "cid": item["contact_id"]},
        )
        if item["target_uid"]:
            await _audit(
                session,
                item["target_uid"],
                _FULFIL_EVENT.get(item["action"], "document.acknowledged"),
                {"request_item": item_uid},
            )

    await session.execute(
        text(
            "UPDATE documents.request_items SET status = 'fulfilled', "
            "  fulfilled_document_id = :doc, fulfilled_at = now() WHERE id = :iid"
        ),
        {"doc": doc_id, "iid": item["id"]},
    )
    # Echo into the client's chat thread too — the mirror of create_request's echo, so the
    # round trip (asked -> fulfilled) is visible in the same conversation, not just the separate
    # Requests surface. Best-effort: a send that cannot resolve the caller (automation, no
    # linked contact) must never block the fulfilment itself.
    try:
        verb = _FULFIL_CHAT_VERB.get(item["action"], "Completed")
        conversation, chat_message = await messaging.send_client_message(
            session, f"{verb}: {item['title']}"
        )
        push_payload = {
            "type": "message",
            "conversation_id": conversation["id"],
            "message": chat_message,
        }
        await live_push.push(live_push.staff_key(tenant_id), push_payload)
        await live_push.push(live_push.client_key(int(item["contact_id"])), push_payload)
    except messaging.MessagingError:
        logger.warning(
            "Best-effort request fulfilment chat echo failed for request item %s",
            item["id"],
            exc_info=True,
        )
    # If this item backs a flow-case step, advance the case (no-op for ordinary requests).
    await flows_orchestrator.on_item_fulfilled(session, int(item["id"]))
    # Roll the parent up to fulfilled once nothing is left pending.
    await session.execute(
        text(
            "UPDATE documents.requests SET status = 'fulfilled', completed_at = now() "
            "WHERE id = :rid AND NOT EXISTS (SELECT 1 FROM documents.request_items "
            "  WHERE request_id = :rid AND status = 'pending')"
        ),
        {"rid": item["request_id"]},
    )
    req = await _request_fetch(session, req_uid)
    if req is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Fulfil failed"
        )
    return req


# Registered imperatively, not with @router.post, purely to give this ONE route the tighter
# ``SingleUploadRoute`` ceiling: it takes a single file and it is the only upload path a *client*
# can reach, so it must not inherit the multi-file drop's allowance. Registered here (in source
# order) so the routing table is unchanged — the path is fully distinct from every other route.
router.add_api_route(
    "/requests/{req_uid}/items/{item_uid}/fulfill",
    fulfill_request_item,
    methods=["POST"],
    name="fulfill-request-item",
    status_code=status.HTTP_201_CREATED,
    route_class_override=SingleUploadRoute,
)


@router.post(
    "/requests/{req_uid}/items/{item_uid}/form-response",
    name="submit-request-form-response",
    status_code=status.HTTP_201_CREATED,
)
async def submit_form_response(
    req_uid: str,
    item_uid: str,
    body: FormResponseIn,
    ctx: FirmContext = Depends(portal_session),
) -> RequestOut:
    """A client submits a completed form for one of their own request items. Runs elevated
    (service_role via firm_session): the parent request must belong to the caller
    (``user_id = auth.uid()``) and the item be a pending ``complete_form`` — that ownership check is
    the entire authorisation boundary, since service_role bypasses RLS. The answers are stored as a
    jsonb blob (keyed by fieldKey) and the item is marked fulfilled; no document is created."""
    session = ctx.session
    item = (
        (
            await session.execute(
                text(
                    "SELECT i.id, i.request_id, i.tenant_id, i.form_uid, i.category, r.contact_id "
                    "FROM documents.request_items i "
                    "JOIN documents.requests r ON r.id = i.request_id "
                    "WHERE i.uid = :iuid AND r.uid = :ruid "
                    "  AND r.contact_id = ANY(auth.contact_scope()) AND i.status = 'pending' "
                    "  AND i.action = 'complete_form'"
                ),
                {"iuid": item_uid, "ruid": req_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request item not found")

    # Prefer the item's form_uid; fall back to the legacy category-carried form code.
    form_uid = item["form_uid"] or item["category"] or ""
    await session.execute(
        text(
            "INSERT INTO documents.form_responses "
            "  (uid, tenant_id, request_item_id, form_uid, contact_id, answers, submitted_by) "
            "VALUES (:uid, :tid, :iid, :form, :cid, CAST(:answers AS jsonb), auth.uid())"
        ),
        {
            "uid": new_id(),
            "tid": int(item["tenant_id"]),
            "iid": item["id"],
            "form": form_uid,
            "cid": item["contact_id"],
            "answers": json.dumps(body.answers),
        },
    )
    await session.execute(
        text(
            "UPDATE documents.request_items SET status = 'fulfilled', fulfilled_at = now() "
            "WHERE id = :iid"
        ),
        {"iid": item["id"]},
    )
    # If this item backs a flow-case step, advance the case (no-op for ordinary requests).
    await flows_orchestrator.on_item_fulfilled(session, int(item["id"]))
    # Roll the parent up to fulfilled once nothing is left pending.
    await session.execute(
        text(
            "UPDATE documents.requests SET status = 'fulfilled', completed_at = now() "
            "WHERE id = :rid AND NOT EXISTS (SELECT 1 FROM documents.request_items "
            "  WHERE request_id = :rid AND status = 'pending')"
        ),
        {"rid": item["request_id"]},
    )
    req = await _request_fetch(session, req_uid)
    if req is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Submit failed"
        )
    return req


class RequestFormOut(BaseModel):
    """The form definition a client renders to complete a `complete_form` request item."""

    form_uid: str
    name: str
    description: str | None
    fields: list[FormFieldOut]
    #: The latest saved or submitted answers, so a return visit continues. Empty until the
    #: first save.
    answers: dict[str, Any] = {}


@router.get(
    "/requests/{req_uid}/items/{item_uid}/form",
    name="get-request-form",
)
async def get_request_form(
    req_uid: str,
    item_uid: str,
    ctx: FirmContext = Depends(portal_session),
) -> RequestFormOut:
    """The builder form + fields for a client to fill a `complete_form` item of their own request.

    Runs elevated (service_role via firm_session): the parent request must belong to the caller
    (``user_id = auth.uid()``) and the item be a ``complete_form`` — that ownership check is the
    whole authorisation boundary (clients have empty tenant lineage, so plain RLS can't serve them
    a tenant-owned form). The form is resolved by the item's ``form_uid`` (legacy: ``category``),
    scoped to the item's tenant so no cross-tenant form can leak."""
    session = ctx.session
    item = (
        (
            await session.execute(
                text(
                    "SELECT i.id, i.form_uid, i.category, i.tenant_id "
                    "FROM documents.request_items i "
                    "JOIN documents.requests r ON r.id = i.request_id "
                    "WHERE i.uid = :iuid AND r.uid = :ruid "
                    "  AND r.contact_id = ANY(auth.contact_scope()) "
                    "  AND i.action = 'complete_form'"
                ),
                {"iuid": item_uid, "ruid": req_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request item not found")
    form_uid = item["form_uid"] or item["category"]
    if not form_uid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="This item has no form attached."
        )
    form = (
        (
            await session.execute(
                text(
                    "SELECT id, uid, name, description FROM documents.forms "
                    "WHERE uid = :uid AND tenant_id = :tid"
                ),
                {"uid": form_uid, "tid": int(item["tenant_id"])},
            )
        )
        .mappings()
        .one_or_none()
    )
    if form is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="The requested form is not available."
        )
    field_rows = (
        (
            await session.execute(
                text(
                    "SELECT uid, field_key, field_type, label, section, description, required, "
                    "       conditions, validation, options, sort_order "
                    "FROM documents.form_fields WHERE form_id = :fid ORDER BY sort_order, id"
                ),
                {"fid": form["id"]},
            )
        )
        .mappings()
        .all()
    )
    stored = (
        await session.execute(
            text(
                "SELECT answers FROM documents.form_responses "
                "WHERE request_item_id = :i ORDER BY id DESC LIMIT 1"
            ),
            {"i": item["id"]},
        )
    ).scalar_one_or_none()
    answers = stored if isinstance(stored, dict) else {}
    return RequestFormOut(
        form_uid=form["uid"],
        name=form["name"],
        description=form["description"],
        fields=[FormFieldOut(**r) for r in field_rows],
        answers=answers,
    )


@router.post(
    "/requests/{req_uid}/items/{item_uid}/form-response/save",
    name="save-request-form-response",
)
async def save_form_response(
    req_uid: str,
    item_uid: str,
    body: FormResponseIn,
    ctx: FirmContext = Depends(portal_session),
) -> dict[str, bool]:
    """Save a client's answers without fulfilling the request item.

    The same ownership check as submit. The item stays pending, so the journey does not
    advance and scoring does not run. Submit is what completes the form.
    """
    session = ctx.session
    item = (
        (
            await session.execute(
                text(
                    "SELECT i.id, i.tenant_id, i.form_uid, i.category, r.contact_id "
                    "FROM documents.request_items i "
                    "JOIN documents.requests r ON r.id = i.request_id "
                    "WHERE i.uid = :iuid AND r.uid = :ruid "
                    "  AND r.contact_id = ANY(auth.contact_scope()) AND i.status = 'pending' "
                    "  AND i.action = 'complete_form'"
                ),
                {"iuid": item_uid, "ruid": req_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request item not found")
    actor = (await session.execute(text("SELECT auth.uid()"))).scalar_one_or_none()
    await form_draft.upsert_draft(
        session,
        tenant_id=int(item["tenant_id"]),
        request_item_id=int(item["id"]),
        form_uid=str(item["form_uid"] or item["category"] or ""),
        contact_id=int(item["contact_id"]) if item["contact_id"] is not None else None,
        answers=body.answers,
        submitted_by=str(actor) if actor is not None else None,
    )
    return {"saved": True}


async def _request_target_ref(
    session: AsyncSession, req_uid: str, item_uid: str
) -> tuple[str, str, str | None, str]:
    """(storage_key, content_type, name, bucket) for the document an agree/acknowledge item asks
    the client to review.

    The ownership check — the request's contact is in the caller's scope, and the item is an
    agree/acknowledge — is the whole authorisation boundary for the two routes below, both of which
    run elevated (``portal_session``): a client legitimately reviews a document they hold no
    ``document_contacts`` link to, so ``/documents/{uid}/content`` is not open to them."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT td.uid AS target_uid "
                    "FROM documents.request_items i "
                    "JOIN documents.requests r ON r.id = i.request_id "
                    "LEFT JOIN documents.documents td ON td.id = i.target_document_id "
                    "WHERE i.uid = :iuid AND r.uid = :ruid "
                    "  AND r.contact_id = ANY(auth.contact_scope()) "
                    "  AND i.action IN ('agree', 'acknowledge')"
                ),
                {"iuid": item_uid, "ruid": req_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not row["target_uid"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    ref = await _original_ref(session, str(row["target_uid"]))
    if ref is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return ref


@router.get("/requests/{req_uid}/items/{item_uid}/target", name="get-request-target")
async def get_request_target(
    req_uid: str, item_uid: str, ctx: FirmContext = Depends(portal_session)
) -> dict[str, str | None]:
    """What an agree/acknowledge item asks the client to review — fetched BEFORE they agree, so
    they never confirm unseen terms. Name + content type; the bytes come from ``/target/content``.

    Deliberately NOT a presigned URL, which is what this returned and why a PDF declaration
    previewed as an empty box. The object store has no public endpoint in a container deployment,
    and even where it does the portal renders a PDF with pdf.js, which FETCHES the bytes rather
    than navigating to them — so a cross-origin URL needs CORS headers the store does not send.
    Both failures look identical to the client: a frame that never fills."""
    _key, content_type, name, _bucket = await _request_target_ref(ctx.session, req_uid, item_uid)
    return {"name": name, "content_type": content_type}


@router.get(
    "/requests/{req_uid}/items/{item_uid}/target/content", name="get-request-target-content"
)
async def get_request_target_content(
    req_uid: str, item_uid: str, ctx: FirmContext = Depends(portal_session)
) -> Response:
    """The reviewable document's bytes, for the portal to proxy same-origin (the client portal's
    ``/api/request-items/…/content``). Same ownership boundary as ``/target`` above."""
    key, ct, name, bucket = await _request_target_ref(ctx.session, req_uid, item_uid)
    try:
        data, got_ct = await storage.get_document(key, bucket=bucket)
    except storage.ObjectMissing as exc:
        # The row is here, its bytes are not: a 404 the viewer can say something honest about,
        # not the 500 botocore's NoSuchKey used to become.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document content not found"
        ) from exc
    except storage.StorageUnavailable as exc:
        # str(exc): the exception distinguishes an unconfigured store from an unreachable one, and
        # sending an operator to check S3_DOCUMENTS_BUCKET when it is set is a wasted afternoon.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    disposition = "inline" if ct in _INLINE_TYPES else "attachment"
    return Response(
        content=data,
        media_type=got_ct or ct,
        headers={"Content-Disposition": _content_disposition(disposition, name or item_uid)},
    )


@router.get("/requests/{req_uid}/items/{item_uid}/terms", name="get-request-terms")
async def get_request_terms(
    req_uid: str, item_uid: str, ctx: FirmContext = Depends(portal_session)
) -> dict[str, str | None]:
    """The client fetches the inline HTML of a terms declaration to review BEFORE acknowledging it.
    Terms are ``document_type='terms'`` documents carrying their HTML in ``metadata.content`` (no
    object storage needed). Same ownership boundary as ``get_request_target`` (the request's contact
    is in the caller's scope, the item is an acknowledge). Returns ``{name, content}``."""
    row = (
        (
            await ctx.session.execute(
                text(
                    "SELECT td.name, "
                    "       COALESCE("
                    "         NULLIF(btrim(td.metadata ->> 'content'), ''), "
                    "         NULLIF(btrim(v.body_html), '')"
                    "       ) AS content "
                    "FROM documents.request_items i "
                    "JOIN documents.requests r ON r.id = i.request_id "
                    "JOIN documents.documents td ON td.id = i.target_document_id "
                    "LEFT JOIN terms.terms t ON t.uid = td.metadata ->> 'term_uid' "
                    "LEFT JOIN terms.term_versions v "
                    "  ON v.id = t.current_published_version_id "
                    "WHERE i.uid = :iuid AND r.uid = :ruid "
                    "  AND r.contact_id = ANY(auth.contact_scope()) "
                    "  AND i.action IN ('agree', 'acknowledge') "
                    "  AND td.document_type = 'terms'"
                ),
                {"iuid": item_uid, "ruid": req_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Terms not found")
    return {"name": row["name"], "content": sanitise_html(row["content"] or "")}


class ReassignItem(BaseModel):
    # The correct document to attach in place of the currently-attached one.
    target_document_uid: str
    # Optional adviser note, recorded on the audit trail (e.g. why the swap was needed).
    reason: str | None = None


@router.post("/requests/{req_uid}/items/{item_uid}/reassign", name="reassign-request-item")
async def reassign_request_item(
    req_uid: str,
    item_uid: str,
    body: ReassignItem,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> RequestOut:
    """Attach the correct document to an agree/acknowledge item (fix a wrong attachment).

    If the client hasn't acted yet, this re-points the item at the new document. If they've ALREADY
    agreed/acknowledged the wrong one, their prior consent can't be edited — it stays in the
    append-only audit as a fact; the item is re-opened (status -> pending, back in the client's
    portal) and a ``document.agreement_superseded`` event records that consent was invalidated.
    Confirm-only actions only (agree/acknowledge carry a target document).

    Gated ``documents:send`` like create: re-pointing an item changes what the client is being
    asked to consent to — client-facing release, not internal drafting."""
    require_permission(principal, DOCUMENTS_SEND)
    item = (
        (
            await session.execute(
                text(
                    "SELECT i.id, i.request_id, i.action, i.status, "
                    "       td.uid AS target_uid, r.contact_id "
                    "FROM documents.request_items i "
                    "JOIN documents.requests r ON r.id = i.request_id "
                    "LEFT JOIN documents.documents td ON td.id = i.target_document_id "
                    "WHERE i.uid = :iuid AND r.uid = :ruid"
                ),
                {"iuid": item_uid, "ruid": req_uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request item not found")
    if item["action"] not in ("agree", "acknowledge"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only agree/acknowledge items reference a document to reassign.",
        )
    if item["status"] == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This item has been cancelled."
        )
    new_doc_id = (
        await session.execute(
            text("SELECT id FROM documents.documents WHERE uid = :uid"),
            {"uid": body.target_document_uid},
        )
    ).scalar_one_or_none()
    if new_doc_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    reopen = item["status"] == "fulfilled"
    # Re-point at the correct document; if already actioned, re-open so the client must act again.
    await session.execute(
        text(
            "UPDATE documents.request_items SET target_document_id = :doc, "
            "  status = CASE WHEN :reopen THEN 'pending' ELSE status END, "
            "  fulfilled_at = CASE WHEN :reopen THEN NULL ELSE fulfilled_at END, "
            "  fulfilled_document_id = CASE WHEN :reopen THEN NULL ELSE fulfilled_document_id END "
            "WHERE id = :iid"
        ),
        {"doc": new_doc_id, "reopen": reopen, "iid": item["id"]},
    )
    if reopen:
        # A re-opened item means the parent request is no longer complete.
        await session.execute(
            text(
                "UPDATE documents.requests SET status = 'pending', completed_at = NULL "
                "WHERE id = :rid"
            ),
            {"rid": item["request_id"]},
        )
    # (Re-)link the client (their CRM contact) to the corrected document for their portal view.
    await session.execute(
        text(
            "INSERT INTO documents.document_contacts "
            "  (document_id, tenant_id, contact_id, role, added_by) "
            "VALUES (:doc, auth.tenant_id(), :cid, 'recipient', auth.uid()) "
            "ON CONFLICT (document_id, contact_id, role) DO NOTHING"
        ),
        {"doc": new_doc_id, "cid": item["contact_id"]},
    )
    if reopen and item["target_uid"]:
        # The client's prior consent to the wrong document is invalidated (kept in the trail).
        await _audit(
            session,
            item["target_uid"],
            "document.agreement_superseded",
            {"request_item": item_uid, "reason": body.reason},
        )
    await _audit(
        session,
        body.target_document_uid,
        "document.reassigned",
        {"request_item": item_uid, "from_document": item["target_uid"], "reason": body.reason},
    )

    req = await _request_fetch(session, req_uid)
    if req is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Reassign failed"
        )
    return req


# ── Document-type taxonomy (managed, per-tenant classification vocabulary) ─────
# The firm's document types are a managed resource (documents.document_types) — staff classify
# against them, a role with `document_types:write` edits them. Defined BEFORE the /{uid} routes so
# GET /documents/types isn't swallowed by GET /documents/{uid}.
class DocumentTypeOut(BaseModel):
    id: int
    # 'document' = a document classification (report, suitability…); 'file' = a supporting-file type
    # (passport, proof of address…). One registry, two vocabularies.
    kind: str
    slug: str
    label: str
    sort_order: int
    archived: bool


class DocumentTypeIn(BaseModel):
    kind: str = "document"
    slug: str
    label: str
    sort_order: int = 0


class DocumentTypeUpdate(BaseModel):
    # None leaves a field unchanged. `archived` soft-retires a type — existing documents keep the
    # slug, but it drops out of the classify pickers.
    label: str | None = None
    sort_order: int | None = None
    archived: bool | None = None


@router.get("/types", name="list-document-types")
async def list_document_types(
    include_archived: bool = False,
    kind: str | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[DocumentTypeOut]:
    """The tenant's type vocabulary (RLS-scoped) for the classify pickers and the admin management
    view. Active types only unless ``include_archived``; ``kind`` filters to 'document' or 'file'
    (both when omitted)."""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id, kind, slug, label, sort_order, archived "
                    "FROM documents.document_types "
                    "WHERE (:include_archived OR archived = false) "
                    # Cast the optional filter so a NULL bind (no kind given) has a concrete type —
                    # otherwise psycopg can't infer `$2 IS NULL` and raises AmbiguousParameter.
                    "  AND (CAST(:kind AS text) IS NULL OR kind = CAST(:kind AS text)) "
                    "ORDER BY kind, sort_order, label"
                ),
                {"include_archived": include_archived, "kind": kind},
            )
        )
        .mappings()
        .all()
    )
    return [DocumentTypeOut(**r) for r in rows]


@router.post("/types", name="create-document-type", status_code=status.HTTP_201_CREATED)
async def create_document_type(
    body: DocumentTypeIn,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> DocumentTypeOut:
    """Add a document or supporting-file type to the tenant's registry (firm-admin:
    ``document_types:write``)."""
    require_permission(principal, DOCUMENT_TYPES_WRITE)
    kind = body.kind.strip().lower()
    if kind not in ("document", "file"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="kind must be 'document' or 'file'.",
        )
    slug = body.slug.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{1,64}", slug):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Slug must be lower_snake_case (a–z, 0–9, underscore), up to 64 chars.",
        )
    label = body.label.strip()
    if not label:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Label is required."
        )
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO documents.document_types "
                    "  (tenant_id, kind, slug, label, sort_order, created_by) "
                    "VALUES (auth.tenant_id(), :kind, :slug, :label, :sort, auth.uid()) "
                    "ON CONFLICT (tenant_id, kind, slug) DO NOTHING "
                    "RETURNING id, kind, slug, label, sort_order, archived"
                ),
                {"kind": kind, "slug": slug, "label": label, "sort": body.sort_order},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:  # ON CONFLICT hit — the slug already exists for this tenant
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"A '{slug}' type already exists."
        )
    return DocumentTypeOut(**row)


@router.patch("/types/{type_uid}", name="update-document-type")
async def update_document_type(
    type_id: Annotated[int, Depends(document_type_id)],
    body: DocumentTypeUpdate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> DocumentTypeOut:
    """Rename / reorder / archive a document type (firm-admin: ``document_types:write``). The
    slug is immutable (it's the key stored on documents)."""
    require_permission(principal, DOCUMENT_TYPES_WRITE)
    # A blank label would violate the table CHECK — reject it cleanly (422) rather than let the
    # constraint surface as a 500 (create_document_type guards the same way).
    if body.label is not None and not body.label.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Label cannot be empty."
        )
    row = (
        (
            await session.execute(
                text(
                    "UPDATE documents.document_types SET label = COALESCE(:label, label), "
                    "  sort_order = COALESCE(:sort, sort_order), "
                    "  archived = COALESCE(:archived, archived) "
                    "WHERE id = :id RETURNING id, kind, slug, label, sort_order, archived"
                ),
                {
                    "label": body.label.strip() if body.label is not None else None,
                    "sort": body.sort_order,
                    "archived": body.archived,
                    "id": type_id,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document type not found")
    return DocumentTypeOut(**row)


class ClassifyRequest(BaseModel):
    filename: str


class ClassifyResult(BaseModel):
    document_type: str
    label: str


@router.post("/classify-type", name="classify-document-type")
async def classify_document_type(
    body: ClassifyRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> ClassifyResult:
    """Suggest a document type for a filename from the tenant's existing tags — a hint, no save.
    Powers the upload dialog's "Auto" mode: the client shows the suggestion for the user to confirm
    or change, then uploads with the concrete tag, so ``auto`` is never persisted. Filename-only
    (no content/LLM). Returns a BLANK suggestion when nothing matches — there is no 'other'/default
    fallback, so the adviser is required to classify manually rather than mis-bucket."""
    require_permission(principal, DOCUMENTS_WRITE)
    slug = await _classify_document_type(session, body.filename or "")
    if not slug:
        return ClassifyResult(document_type="", label="")
    label = (
        await session.execute(
            text(
                "SELECT label FROM documents.document_types WHERE kind = 'document' AND slug = :s"
            ),
            {"s": slug},
        )
    ).scalar_one_or_none() or _labelize(slug)
    return ClassifyResult(document_type=slug, label=label)


# Filename hints for the SEEDED default slugs, so a common upload lands on the right managed type
# even when its name doesn't contain the type's label verbatim (passport → identity, HMRC → tax).
# Custom tenant types still match on their own slug/label tokens below, so a firm's bespoke
# vocabulary is never shadowed by these.
_TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "report": ("report", "suitability", "review", "recommendation", "advice"),
    "illustration": ("illustration", "projection", "quote", "quotation", "kfi"),
    "statement": ("statement", "valuation", "portfolio", "holdings"),
    "agreement": ("agreement", "terms", "contract", "declaration", "mandate", "authority"),
    "application": ("application", "apply", "onboarding", "enrolment"),
    "letter": ("letter", "correspondence"),
    "identity": ("identity", "passport", "licence", "license", "kyc", "id"),
    "tax": ("tax", "hmrc", "return", "utr", "p60", "p45"),
    # The IRS names its own downloads fw9.pdf and fw8ben.pdf. A hyphenated "W-8BEN" is matched
    # through the hyphen-joined tokens in `_pick_document_type`.
    "fatca_form": ("fatca", "w9", "w8", "fw9", "w8ben", "w8bene", "fw8ben", "fw8bene", "8ben"),
}

# Slug and label words that say what KIND of paper a type is, not what it is about. `fatca_form`
# carries "form", and "Transfer form.pdf" is not a W-9.
_GENERIC_KEYWORDS = frozenset({"form"})


def _pick_document_type(filename: str, types: list[dict[str, str]]) -> str:
    """Best-matching type slug for ``filename`` from ``types`` (each ``{slug, label}``), by
    whole-word keyword overlap — the type's own slug/label tokens plus a light hint list for the
    seeded defaults. Returns ``''`` when nothing matches: there is NO 'other'/first-type fallback,
    so the caller leaves the type blank and the adviser classifies manually — a document is never
    silently mis-bucketed. Pure and filename-only (no content/LLM); staff can always reclassify.

    The filename is also read with its hyphens removed, so "W-9.pdf" offers the token "w9"."""
    lowered = filename.lower()
    name_tokens = set(re.findall(r"[a-z0-9]+", lowered))
    name_tokens |= set(re.findall(r"[a-z0-9]+", lowered.replace("-", "")))
    if not name_tokens:
        return ""
    best_slug, best_score = "", 0
    for t in types:
        keywords = set(re.findall(r"[a-z0-9]+", f"{t['slug']} {t['label']}".lower()))
        keywords |= set(_TYPE_HINTS.get(t["slug"], ()))
        keywords = {k for k in keywords if len(k) >= 2 and k not in _GENERIC_KEYWORDS}
        score = len(keywords & name_tokens)
        if score > best_score:
            best_slug, best_score = t["slug"], score
    return best_slug


async def _classify_document_type(session: AsyncSession, filename: str) -> str:
    """Auto-classify an upload from its filename against the tenant's active DOCUMENT types (RLS).

    Scoped to ``kind = 'document'``: 0040 added supporting-FILE types (passport, national_id…) to
    the same registry, and without this filter a filename like ``client_passport_scan.pdf`` matches
    the file type 'passport' and shadows the document type 'identity' — but this endpoint powers the
    Document tab, which classifies into the document taxonomy only."""
    types = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT uid, slug, label FROM documents.document_types "
                    "WHERE archived = false AND kind = 'document' ORDER BY sort_order, slug"
                )
            )
        )
        .mappings()
        .all()
    ]
    return _pick_document_type(filename, types)


@router.get("/{uid}", name="get-document")
async def get_document(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(book_session),
) -> DocumentOut:
    """Metadata for one document (RLS-scoped)."""
    doc = await _fetch(session, uid)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


async def _is_client(session: AsyncSession) -> bool:
    """Is the caller a client end-user? Derived in SQL from the caller's own claims (never a
    request parameter), mirroring search.py. Belt AND braces, exactly as ``0004_portal_scope.sql``
    writes its own policies (``auth.portal() = 'client' OR auth.role() = 'client'``):
    ``auth.portal()`` is NULL whenever the tenant's environment classification is missing, and on
    a NULL portal claim a client session must not fall through to the staff branch."""
    scope = (
        (await session.execute(text("SELECT auth.portal() AS portal, auth.role() AS role")))
        .mappings()
        .one()
    )
    return bool(scope["portal"] == "client" or scope["role"] == "client")


async def _require_staff_documents_read(session: AsyncSession, principal: Principal) -> None:
    """Download gate for the two content routes below. Clients stay RLS-only — they must be able
    to reach their own documents, and RLS already restricts them to exactly those. STAFF need
    ``documents:read``: /files is gated, so without this branch a zero-permission staff login
    could download any tenant-visible document by walking this route instead."""
    if not await _is_client(session):
        require_permission(principal, DOCUMENTS_READ)


@router.get("/{uid}/url", name="document-url")
async def document_url(
    uid: str,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> dict[str, str]:
    """A short-lived (15-min) presigned download URL for the document's original file."""
    await _require_staff_documents_read(session, principal)
    ref = await _original_ref(session, uid)
    if ref is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    key, _ct, name, bucket = ref
    try:
        url = await storage.presigned_get_document(
            key, filename=name or uid, disposition="inline", bucket=bucket
        )
    except storage.StorageUnavailable as exc:
        # str(exc): the exception distinguishes an unconfigured store from an unreachable one, and
        # sending an operator to check S3_DOCUMENTS_BUCKET when it is set is a wasted afternoon.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    await _audit(session, uid, "document.viewed")
    # Fold the view into the action stream too — a GET the request-path middleware never sees, so a
    # firm's Events view would otherwise show who a document was SENT to but never who OPENED it.
    # POST-response (like the middleware's own floor): its own service_role connection must not be
    # held on the view's critical path beside the request session.
    background_tasks.add_task(
        record_document_action,
        event_type=DOCUMENT_VIEW_EVENT,
        method="GET",
        route="/documents/{uid}/url",
        document_uid=uid,
        principal=principal,
    )
    return {"url": url}


# A preview (grid thumbnail) may be reused briefly; a real view stays uncached.
_PREVIEW_CACHE = "private, max-age=300"


@router.get("/{uid}/content", name="document-content")
async def document_content(
    uid: str,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
    preview: bool = False,
) -> Response:
    """Proxy the document's original bytes. PDFs/images inline; everything else as an attachment.
    A terms declaration serves its sanitised HTML, CSP-sandboxed. ``preview``: no view recorded,
    briefly cacheable, 204 for a type a browser can't draw."""
    await _require_staff_documents_read(session, principal)
    ref = await _original_ref(session, uid)
    headers: dict[str, str] = {}
    if ref is None:
        terms = await _terms_content(session, uid)
        if terms is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        name, html = terms
        data = sanitise_html(html).encode("utf-8")
        ct = got_ct = "text/html; charset=utf-8"
        disposition = "inline"
        headers = {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"}
    else:
        key, ct, name, bucket = ref
        if preview and ct not in _INLINE_TYPES:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        try:
            data, got_ct = await storage.get_document(key, bucket=bucket)
        except storage.ObjectMissing as exc:
            # The row is here, its bytes are not: a 404 the viewer can say something honest about,
            # not the 500 botocore's NoSuchKey used to become.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Document content not found"
            ) from exc
        except storage.StorageUnavailable as exc:
            # str(exc): it tells an unconfigured store from an unreachable one.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        disposition = "inline" if ct in _INLINE_TYPES else "attachment"
    if preview:
        headers["Cache-Control"] = _PREVIEW_CACHE
    else:
        # The default viewer path, so it records the view like /url does. Off the critical path.
        await _audit(session, uid, "document.viewed")
        background_tasks.add_task(
            record_document_action,
            event_type=DOCUMENT_VIEW_EVENT,
            method="GET",
            route="/documents/{uid}/content",
            document_uid=uid,
            principal=principal,
        )
    headers["Content-Disposition"] = _content_disposition(disposition, name or uid)
    return Response(content=data, media_type=got_ct or ct, headers=headers)


class UpdateDocument(BaseModel):
    name: str | None = None
    # Open-ended type: a retype registers the tag in the tenant's registry (a reusable suggestion),
    # so the rest of the product can represent it. None leaves the type unchanged.
    document_type: str | None = None
    # Assign the made-for client (``intended_contact_id``): "Add from existing documents" on the
    # client record files an existing firm document under a client. None leaves it unchanged (no
    # clear-here path); the target must be a contact the caller sees, enforced by RLS on the lookup.
    # Filing is a DISCLOSURE — the named client can then see the document (a 'subject' link) — so
    # the visibility check on that lookup is load-bearing, not merely tidy.
    intended_contact_id: int | None = None


@router.patch("/{uid}", name="update-document")
async def update_document(
    uid: str,
    body: UpdateDocument,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> DocumentOut:
    """Rename or reclassify a document (name / document_type)."""
    require_permission(principal, DOCUMENTS_WRITE)
    if body.name is not None or body.document_type is not None:
        # Open-ended reclassify: register the tag (returns the stored slug) so it becomes a reusable
        # suggestion; None leaves the type unchanged.
        dtype = (
            await _register_document_type(session, body.document_type)
            if body.document_type is not None
            else None
        )
        row = (
            await session.execute(
                text(
                    "UPDATE documents.documents SET name = COALESCE(:name, name), "
                    "document_type = COALESCE(:dtype, document_type) "
                    "WHERE uid = :uid RETURNING id"
                ),
                {"name": body.name, "dtype": dtype, "uid": uid},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        changed: dict[str, object] = {
            k: v for k, v in (("name", body.name), ("document_type", dtype)) if v is not None
        }
        await _audit(session, uid, "document.updated", changed)
    if body.intended_contact_id is not None:
        # Assign the made-for client. The contact must be one the caller can see (RLS on
        # crm.contacts), so a staffer can't file a document under a client outside their book. A
        # distinct audit action so the record shows a document was filed, not "renamed".
        seen = (
            await session.execute(
                text("SELECT 1 FROM crm.contacts WHERE id = :cid"),
                {"cid": body.intended_contact_id},
            )
        ).first()
        if seen is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        row = (
            await session.execute(
                text(
                    "UPDATE documents.documents SET intended_contact_id = :cid "
                    "WHERE uid = :uid RETURNING id, status::text AS status"
                ),
                {"cid": body.intended_contact_id, "uid": uid},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        # Filing it under this client makes it visible to them (see docs_service.link_subject). On a
        # RE-file the previous client's 'subject' link is deliberately LEFT IN PLACE: they have
        # already been able to see the document, so removing the link would silently retract
        # something a client may have read and acted on — a decision for an explicit unshare
        # endpoint, not a side effect of correcting the "Client" column. A mis-filing is therefore
        # a disclosure to fix deliberately, which is the safer of the two failure modes to have.
        # ...but only for a document that EXISTS to be read. A `queued` one has not rendered and a
        # `failed` one never will, and the client branch of `documents_read` has no status
        # predicate — so linking either would show the client a document no adviser has seen. For a
        # queued one `worker._persist` files the link off `intended_contact_id` when the render
        # succeeds, which is the same rule the generate path now follows; a failed one stays
        # invisible.
        tenant_id = await _tenant_id(session)
        if row[1] not in ("queued", "failed"):
            await docs_service.link_subject(
                session,
                tenant_id=tenant_id,
                document_id=row[0],
                contact_id=body.intended_contact_id,
            )
        await _audit(session, uid, "document.assigned", {"contact_id": body.intended_contact_id})
    doc = await _fetch(session, uid)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


class CancelRenderOut(BaseModel):
    """What cancelling actually achieved, which is not always "it stopped"."""

    #: True when the job was still waiting and will now never be claimed.
    stopped: bool
    #: True when a worker already had it. The render cannot be interrupted mid-flight, so it will
    #: finish — the document is archived either way, so it does not reappear as new work.
    already_rendering: bool
    detail: str


@router.post("/{uid}/cancel-render", name="cancel-document-render")
async def cancel_document_render(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> CancelRenderOut:
    """Stop waiting for a document that has not rendered, and stop it being rendered.

    The wait can be minutes — a queue with nothing draining it waits for ever — and until now the
    only way out of the generating screen was to navigate away, which left the job queued to
    surprise the operator later.

    IT REPORTS WHAT IT ACHIEVED RATHER THAN CLAIMING SUCCESS. A job that is still `queued` is
    marked `cancelled`, and the worker's claim only ever selects `queued` or a STALE `processing`
    row, so nothing will pick it up. A job a worker already holds cannot be interrupted — the
    render is running in another process — so this says so instead of pretending, and the document
    is archived either way so a late finish does not put unexpected work on the list.

    Archived, not deleted: the row records who generated what and when, and the inputs they used.
    Cancelling is a decision about a document, not a reason to lose the evidence of it. It restores
    through the existing `/unarchive`, which remembers the status it had.
    """
    require_permission(principal, DOCUMENTS_WRITE)
    doc = await _fetch(session, uid)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if doc.status == "generated":
        # Nothing to cancel, and saying "cancelled" about a finished document would be a lie: the
        # PDF exists and may already have been sent.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document has already been generated. Archive it instead.",
        )

    # A CTE so RETURNING can carry the status the job HAD: after the UPDATE, `j.status` is
    # 'cancelled' for every row, which cannot tell "it was waiting" from "a worker had it".
    jobs = (
        (
            await session.execute(
                text(
                    "WITH was AS ("
                    "  SELECT j.id, j.status"
                    "    FROM documents.render_jobs j"
                    "    JOIN documents.documents d ON d.id = j.document_id"
                    "   WHERE d.uid = :uid AND j.status IN ('queued', 'processing')"
                    ") "
                    "UPDATE documents.render_jobs j SET status = 'cancelled', updated_at = now() "
                    "FROM was WHERE j.id = was.id "
                    "RETURNING was.status AS was_status"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .all()
    )
    already_rendering = any(j["was_status"] == "processing" for j in jobs)

    await session.execute(
        text(
            "UPDATE documents.documents SET status = 'archived', "
            "  metadata = metadata || jsonb_build_object('archived_from', status::text, "
            "                                            'cancelled', true) "
            "WHERE uid = :uid AND status <> 'archived'"
        ),
        {"uid": uid},
    )
    await _audit(session, uid, "document.render_cancelled")

    if already_rendering:
        detail = (
            "A worker had already started rendering this, and a render in progress cannot be "
            "stopped — it may still finish. The document is archived, so it will not appear on "
            "your list."
        )
    elif jobs:
        detail = "Cancelled. Nothing will render this document."
    else:
        detail = "There was nothing left to render. The document is archived."
    return CancelRenderOut(
        stopped=bool(jobs) and not already_rendering,
        already_rendering=already_rendering,
        detail=detail,
    )


@router.post("/{uid}/archive", name="archive-document")
async def archive_document(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> DocumentOut:
    """Soft-archive a document (status='archived'): drops off the working list (``?status=active``,
    the default) but stays queryable via ``?status=archived`` — the wire parameter is ``status``, as
    ``GET /documents`` declares it — and is restorable with ``/unarchive``. The status it
    had is remembered in ``metadata.archived_from`` so restoring is exact — a failed or still-queued
    document doesn't come back pretending to be a finished one.

    Refuses a document marked ``deletable = false`` (0122) — one the firm does not own, e.g. a
    record synced from a custodian. Checked BEFORE the UPDATE and as its own 409 so the reason can
    be named: folding it into the UPDATE's WHERE would make an undeletable document look exactly
    like an already-archived one, which this endpoint deliberately treats as success."""
    require_permission(principal, DOCUMENTS_WRITE)
    protected = (
        await session.execute(
            text("SELECT deletable FROM documents.documents WHERE uid = :uid"),
            {"uid": uid},
        )
    ).scalar_one_or_none()
    if protected is False:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This document is supplied by a custodian and cannot be deleted here.",
        )
    row = (
        await session.execute(
            text(
                "UPDATE documents.documents SET status = 'archived', "
                "  metadata = metadata || jsonb_build_object('archived_from', status::text) "
                "WHERE uid = :uid AND status <> 'archived' RETURNING id"
            ),
            {"uid": uid},
        )
    ).first()
    if row is None:
        # Already archived is not an error (the bulk bar retries); anything else is a 404.
        doc = await _fetch(session, uid)
        if doc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        return doc
    await _audit(session, uid, "document.archived")
    doc = await _fetch(session, uid)
    if doc is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Archive failed"
        )
    return doc


@router.post("/{uid}/unarchive", name="unarchive-document")
async def unarchive_document(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> DocumentOut:
    """Restore an archived document to the working list — archiving is recoverable, which is what
    the confirmation copy is allowed to promise. It returns to the status it held before archiving
    (``metadata.archived_from``, else ``generated``); a document archived before that was recorded
    comes back as ``generated``, which is the live state uploads and rendered documents hold.
    Idempotent: an already-live document is returned unchanged."""
    require_permission(principal, DOCUMENTS_WRITE)
    row = (
        await session.execute(
            text(
                "UPDATE documents.documents SET status = CAST("
                "    COALESCE(metadata ->> 'archived_from', 'generated') "
                "    AS documents.document_status), "
                "  metadata = metadata - 'archived_from' "
                "WHERE uid = :uid AND status = 'archived' RETURNING id"
            ),
            {"uid": uid},
        )
    ).first()
    if row is None:
        doc = await _fetch(session, uid)
        if doc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        return doc  # not archived — nothing to restore
    await _audit(session, uid, "document.unarchived")
    doc = await _fetch(session, uid)
    if doc is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unarchive failed"
        )
    return doc


@router.post("/{uid}/retry", name="retry-document")
async def retry_document(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> DocumentOut:
    """Re-queue a failed render — a dead-lettered document is otherwise terminal and the adviser has
    to generate a fresh one.

    Idempotent: a fresh ``render_jobs`` row is enqueued only when the document is ``failed`` (or is
    ``queued`` with no live job — the enqueue half of a lost transaction). A document already
    waiting on a live job is returned untouched, so a double-click can't queue two renders. The
    dead-lettered job row is left as history (``authenticated`` may only INSERT here, by design).

    The live-job read and the enqueue are serialised on a per-document advisory lock, the same
    discipline ``generate_document`` uses for its idempotency key: ``render_jobs`` carries no
    unique constraint on ``document_id``, so a plain check-then-INSERT lets two parallel retries
    both see no live job and both enqueue — two renders racing on the same ``pdf_file_id``."""
    require_permission(principal, DOCUMENTS_WRITE)
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "  hashtextextended(auth.tenant_id()::text || ':retry:' || :u, 0))"
        ),
        {"u": uid},
    )
    row = (
        (
            await session.execute(
                text(
                    "SELECT d.id, d.status::text AS status, d.source, "
                    "       d.template_version_id, "
                    "       EXISTS (SELECT 1 FROM documents.render_jobs j "
                    "         WHERE j.document_id = d.id "
                    "           AND j.status IN ('queued', 'processing')) AS live_job "
                    "FROM documents.documents d WHERE d.uid = :uid"
                ),
                {"uid": uid},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if row["source"] != "generated" or row["template_version_id"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only a generated document can be re-rendered.",
        )
    if row["status"] not in ("failed", "queued"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a failed document can be retried.",
        )
    if row["live_job"]:
        doc = await _fetch(session, uid)
        if doc is None:  # pragma: no cover
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        return doc  # already waiting on the worker — nothing to do
    await session.execute(
        text("UPDATE documents.documents SET status = 'queued', error = NULL WHERE uid = :uid"),
        {"uid": uid},
    )
    await session.execute(
        text(
            "INSERT INTO documents.render_jobs (tenant_id, document_id) "
            "VALUES (auth.tenant_id(), :doc)"
        ),
        {"doc": row["id"]},
    )
    await _audit(session, uid, "document.queued", {"retry": True})
    doc = await _fetch(session, uid)
    if doc is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Retry failed"
        )
    return doc


@router.get("/{uid}/requests", name="list-requests-for-document")
async def list_requests_for_document(
    uid: str,
    session: AsyncSession = Depends(tenant_session),
) -> list[RequestOut]:
    """Requests touching this document — an item targets it (agree/acknowledge) or was fulfilled by
    it (upload). RLS-scoped; per-document view of the requests flow (replaces document_tasks)."""
    doc_id = (
        await session.execute(
            text("SELECT id FROM documents.documents WHERE uid = :uid"), {"uid": uid}
        )
    ).scalar_one_or_none()
    if doc_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    req_ids = (
        (
            await session.execute(
                text(
                    "SELECT DISTINCT request_id FROM documents.request_items "
                    "WHERE :doc IN (target_document_id, fulfilled_document_id)"
                ),
                {"doc": doc_id},
            )
        )
        .scalars()
        .all()
    )
    if not req_ids:
        return []
    headers = (
        (
            await session.execute(
                text(_REQ_HEADER + "WHERE r.id = ANY(:ids) ORDER BY r.created_at DESC"),
                {"ids": req_ids},
            )
        )
        .mappings()
        .all()
    )
    return await _build_requests(session, [dict(h) for h in headers])
