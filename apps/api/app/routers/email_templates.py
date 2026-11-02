"""Email templates — the firm-facing email-authoring surface (persistence for the platform editor).

Firm staff author versioned, per-tenant email templates (invitations, notifications, client comms).
This is the authoring half — a template record (``email.templates``) plus its version history
(``email.template_versions``), both tenant-scoped. Runs under ``tenant_session`` (RLS
``authenticated`` enforces tenant isolation); each action is gated by an ``email_templates:*``
permission.

The persistence itself — create / save-version / publish / restore / clone — lives in
``app/email/templates_service.py`` so this firm router and the admin model-authoring router share
ONE copy of the SQL (the model-tenant preset is authored through the admin surface). This router is
the thin RLS-bound half: it resolves the acting tenant + user from the session claims
(``auth.tenant_id()`` / ``auth.uid()``) and hands them to the service, which is HTTP-free and
raises the domain errors mapped below.
"""

from datetime import datetime
from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import imaging, ratelimit, storage, tenant_storage
from app.auth import Principal, get_current_principal, require_permission
from app.comms import ledger
from app.db import target_tenant_session as tenant_session
from app.email import catalog as email_catalog
from app.email import defaults as email_defaults
from app.email import preview as email_preview
from app.email import templates_service as svc
from app.email import transport as email_mod
from app.permissions import EMAIL_TEMPLATES_READ, EMAIL_TEMPLATES_WRITE
from app.routers.documents import CappedBodyRoute

router = APIRouter(prefix="/email-templates", tags=["email-templates"])

# The catalogue's own type, not a second copy of it: this is the one pydantic ENFORCES on a create
# or update body, so a category the catalogue offers and this module has never heard of is a 422
# from a module that does not mention the catalogue — a refusal naming nothing the author chose.
Category = email_catalog.Category

# Who a preview is dressed for. Maps onto the two brand portals: a client-portal recipient wears the
# `client` branding, a member of the firm's staff the `platform` one.
Audience = Literal["client", "staff"]


class EmailTemplateOut(BaseModel):
    uid: str
    code: str
    name: str
    category: str
    description: str | None
    version_count: int
    current_version_number: int | None
    current_version_status: str | None
    created_at: datetime
    created_by: str | None


class EmailVersionOut(BaseModel):
    uid: str
    version_number: int
    status: str
    subject: str
    preheader: str
    created_at: datetime
    created_by: str | None
    published_at: datetime | None
    published_by: str | None
    # Which SIDE authored this version — 'firm' (the tenant's own staff) or 'operations' (Graphene,
    # acting on the firm's behalf). None for versions written before 0051, whose authorship is
    # genuinely unknown. `created_by`/`published_by` name a PERSON, which cannot tell a firm that
    # the person was one of ours; this is how the UI can.
    authored_by_scope: str | None = None


class EmailVersionDetailOut(EmailVersionOut):
    # `schema` is the editor's content (html/css/gjs). Field is `schema_content` internally to avoid
    # any clash with pydantic internals; the wire name is `schema` via the alias.
    schema_content: dict[str, Any] = Field(default_factory=dict, alias="schema")
    model_config = {"populate_by_name": True}


class PublishEmailResultOut(EmailVersionDetailOut):
    # The published version, plus a soft warning: merge tokens the version references that nothing
    # supplies, so they render BLANK on every send. Non-blocking — publish still succeeds. Same
    # shape and posture as the document side's `PublishResultOut.unknown_variables`.
    #
    # INCOMPLETE by construction, and the copy an author reads must say so: a token behind an
    # `{% if %}` is never evaluated, and a present-but-null value is defined rather than unsourced.
    # Empty for a code the catalogue does not know — see `email/preview.py::scoped_codes`.
    unsourced: list[str] = []


class CreateEmailTemplate(BaseModel):
    code: str
    name: str
    category: Category = "notification"
    description: str | None = None
    # What the email is ABOUT (`contact`/`account`/`task`/`document`). Asked at creation because it
    # decides which event details the editor may list; empty = about nothing in particular.
    subjects: list[str] | None = None


class UpdateEmailTemplate(BaseModel):
    name: str | None = None
    code: str | None = None
    category: Category | None = None
    description: str | None = None


class SaveEmailVersion(BaseModel):
    subject: str = ""
    preheader: str = ""
    schema_content: dict[str, Any] = Field(default_factory=dict, alias="schema")
    model_config = {"populate_by_name": True}


class PreviewEmail(BaseModel):
    """The editor's current (possibly unsaved) content to render a sample preview from."""

    subject: str = ""
    preheader: str = ""
    html: str = ""
    css: str = ""
    # Whose brand the preview is dressed in. WHO an email goes to is decided at SEND time, not by
    # the template's category: `document_sent`/`request_raised` always go to a
    # client, `invite`/`password_reset` go to whichever portal the person belongs to (see
    # provisioning.py, which resolves the brand from the portal being granted), and the two task
    # codes follow the assignee. So the caller says which audience it wants to see, defaulting to
    # the client, rather than inferring it from a field that doesn't carry that fact.
    audience: Audience = "client"


class PreviewEmailOut(BaseModel):
    subject: str
    preheader: str
    html: str
    # Tokens the content referenced that had no sample value (rendered empty).
    missing: list[str]


class SendTestEmail(PreviewEmail):
    """The same live canvas ``preview`` takes — subject / preheader / html / css / audience — mailed
    for real, to the AUTHENTICATED CALLER'S OWN address.

    **There is deliberately no recipient field, and unknown keys are IGNORED** (pydantic's default,
    pinned by a test). An endpoint that accepted an address would be an authenticated open relay:
    any member of a firm's staff could aim the firm's own verified sending identity at a client,
    with arbitrary HTML on it, and the audit trail would show a template test. The recipient is
    resolved server-side from the session claims and nothing in the request body can influence it.
    """


class SendTestEmailOut(BaseModel):
    """The outcome of a test send, three-valued on purpose.

    ``send_email`` answers a single bool (a suppressed send is, to a caller, exactly as undelivered
    as a failed one), but an AUTHOR needs the difference: "nothing left the building because this
    deployment mails nobody" is a configuration fact they can act on, while "we tried and Resend
    refused" is an incident. Collapsing the two would have an author on a staging box hunting a
    delivery bug that does not exist — or, worse, reading a silent suppression as success."""

    status: Literal["delivered", "suppressed", "failed"]
    # The caller's OWN address, echoed back so the UI can say where to look. This is the only
    # address that ever appears in this response — see ``SendTestEmail``.
    to: str
    # The delivered subject, INCLUDING the test marker, so the author knows what to search for.
    subject: str
    # For 'suppressed', ``suppression_reason``/``sending_disabled``'s text — which names the SETTING
    # and never the recipient. For 'failed', a short message; the exception itself is logged by
    # ``send_email``, not returned (it can carry provider detail).
    detail: str | None = None
    # The same missing-token diagnostic ``preview`` returns, on the same content.
    missing: list[str] = Field(default_factory=list)


class EmailCodeOut(BaseModel):
    code: str
    label: str
    category: str
    description: str


class EmailDefaultOut(BaseModel):
    subject: str
    # The shipped preview-text line. Returned so "Load default" fills the preheader field too —
    # without it an author loaded the default, published it, and their override delivered no inbox
    # preview line although the shipped default they replaced does.
    preheader: str
    # THE EDITOR'S SHAPE, not the render-ready one. "Load default" seeds a CANVAS, and the canvas
    # must
    # be fed the copy whose conditionals are `data-if` attributes rather than Jinja text — the panel
    # can
    # then read and edit them, and no code chips appear. Feeding it the render-ready shape is the
    # round-trip damage `pkgs/ui`'s `conditions/round-trip.test.ts` exists to prevent: the loose
    # `{% if %}` becomes ordinary content and the next save writes it back as authored text.
    #
    # `html` keeps its name and its meaning for every other caller (the preview, the send path), so
    # this
    # is an addition rather than a rename.
    html: str
    css: str
    source: str


def _raise_http(exc: svc.EmailTemplateError) -> NoReturn:
    """Map a service domain error onto the HTTP response the firm surface expects."""
    if isinstance(exc, svc.DuplicateCode):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An email template with this code already exists.",
        )
    if isinstance(exc, svc.TemplateNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Email template not found"
        )
    if isinstance(exc, svc.VersionNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    if isinstance(exc, svc.NotADraft):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Only a draft version can be published."
        )
    if isinstance(exc, svc.PublishedImmutable):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This version was published while you were editing, and a published email is "
                "immutable. Reload the template to continue from a new draft."
            ),
        )
    if isinstance(exc, svc.PublishBlocked):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.reason)
    # A claim the editor should never have posted — 422 with the reason, which names the offending
    # kind so the surface can show it against the checkbox being edited.
    if isinstance(exc, svc.InvalidSubjects):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.reason)
    if isinstance(exc, svc.VersionConflict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Another change created a new version of this template just now. Reload the "
                "template and try again."
            ),
        )
    raise HTTPException(  # pragma: no cover — defensive
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Email template error"
    )


async def _ctx(session: AsyncSession) -> tuple[int, str]:
    """The acting (tenant_id, user_id) from the RLS session claims, passed to the service."""
    row = (
        (await session.execute(text("SELECT auth.tenant_id() AS t, auth.uid() AS u")))
        .mappings()
        .one()
    )
    return int(row["t"]), str(row["u"])


def _render_or_422(**kwargs: Any) -> email_preview.PreviewResult:
    """Render the editor's content, or refuse with the engine's own words.

    A template the engine cannot render raises rather than reporting a blank — an author who typed
    `{% if %}` got a 500, which reads as the platform being broken instead of as their own
    half-finished condition. The catch is BARE, because a filter raises its own error and not a
    `TemplateError`: Jinja does not wrap what a filter throws, so `{{ x | round(2) }}` on a string
    surfaces a plain `TypeError` and any arity or type mistake an author can type surfaces
    something else again. Only the parse errors are `TemplateError`s, and they are the smaller half.
    Same shape the flow tier's refusal uses (`flows/effects.py`), so a broken template reads
    the same wherever it is rendered, and the same bare catch `templates_service._render_failure`
    already uses over these calls.

    The message is client-safe: an engine error names the construct, never the context.

    An UNDEFINED value is deliberately not this: `render_with_diagnostics` reports it in `missing`,
    which the response carries, and refusing a blank would block a legitimate draft.
    """
    try:
        return email_preview.render_preview(**kwargs)
    except Exception as exc:  # noqa: BLE001 — a filter's own error is not a TemplateError
        # `getattr`, because only a `TemplateError` carries `.message` — reading it off a
        # `TypeError` would raise from inside the handler and restore the 500 this refuses.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This email cannot be previewed. Check its conditions and merge fields.",
        ) from exc


async def build_preview(
    session: AsyncSession, tenant_id: int, code: str, body: "PreviewEmail"
) -> "PreviewEmailOut":
    """Render the editor's current content against sample values in the tenant's brand — shared by
    the firm preview endpoint and the admin model-preview endpoint (one preview implementation).
    ``audience`` picks which of the tenant's two brands (client vs platform) dresses the preview."""
    portal: Literal["client", "platform"] = "platform" if body.audience == "staff" else "client"
    brand = await email_mod.brand_for(session, tenant_id, portal=portal)
    result = _render_or_422(
        subject=body.subject,
        preheader=body.preheader,
        html=body.html,
        css=body.css,
        brand=brand,
        code=code,
        viewer_first_name=await _own_first_name(session),
    )
    return PreviewEmailOut(
        subject=result.subject,
        preheader=result.preheader,
        html=result.html,
        missing=result.missing,
    )


#: Prepended to the subject of a test send. A SUBJECT prefix rather than a banner in the body,
#: because the body is the thing under test — an injected banner would change the very layout
#: (table widths, dark-mode inversion, the preheader the author is checking) that sending to a real
#: inbox exists to reveal. It also shows in an inbox LIST view, before anything is opened, and
#: survives plain-text/notification previews that render no HTML at all. Kept ASCII and bracketed so
#: it reads as machine-added rather than as copy someone wrote.
TEST_SUBJECT_PREFIX = "[TEST] "


async def _own_first_name(session: AsyncSession) -> str | None:
    """The acting caller's own first name, or None.

    Used only to sample ``{{ first_name }}`` as the person looking at the email rather than as
    "Sarah" — see ``email_preview.sample_context``. Never used to address a real send: this is a
    preview value, and the real recipient's name comes from the send site's own context.

    Failure is not an error. An operator on the admin door holds no row in the firm's tenant, and a
    user row may carry no first name at all; either way the fixture stands in, so this returns None
    rather than refusing. Contrast :func:`_own_identity`, where an unresolvable value MUST refuse,
    because there it decides who gets mailed."""
    return (
        await session.execute(
            text("SELECT (SELECT first_name FROM auth.users WHERE id = auth.uid())")
        )
    ).scalar_one_or_none()


async def _own_identity(session: AsyncSession) -> tuple[str, str]:
    """The acting caller's ``(user_uuid, email)`` from the session's own claims.

    ``auth.uid()`` + the caller's own ``auth.users`` row — readable under RLS by the self branch of
    ``auth_users_scoped_read``, and under the admin session's ``service_role``. This is the ONLY
    source of a test send's recipient: not a parameter, not a lookup by name, not the tenant's
    support address. A caller with no resolvable address is refused with a message that says so,
    rather than falling back to anything."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT auth.uid() AS user_id, "
                    "       (SELECT email FROM auth.users WHERE id = auth.uid()) AS email"
                )
            )
        )
        .mappings()
        .one()
    )
    user_id = str(row["user_id"] or "")
    email = str(row["email"] or "").strip()
    if not user_id or not email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "A test email can only be sent to your own address, and this account has no email "
                "address on record. Ask an administrator to set one, then try again."
            ),
        )
    return user_id, email


async def send_test(
    session: AsyncSession, tenant_id: int, code: str, body: "SendTestEmail"
) -> "SendTestEmailOut":
    """Render the editor's CURRENT content exactly as :func:`build_preview` does and mail it to the
    caller's own address, through ``email.send_email`` (the one chokepoint, so the non-production
    guard applies unchanged). Shared by the firm and admin doors — one implementation, so a test
    send and a preview can never disagree about what the email looks like.

    Refuses a subjectless canvas with 422 — see :data:`TEST_SUBJECT_PREFIX` and the check below.

    The envelope identity comes from ``resolve_sender`` (a real DB read against the session in
    hand), never ``sender_for`` — see that function. An author's first test of the day would
    otherwise arrive from the Graphene address and prove nothing about their own deliverability."""
    portal: Literal["client", "platform"] = "platform" if body.audience == "staff" else "client"
    # Refused FIRST: cheapest, before a DB read, a throttle slot or a byte of render — and it is
    # the same gate ``publish_version`` applies, in the same voice. A test send exists for inbox
    # fidelity, and the subject is the largest thing an inbox shows; one delivered as a bare
    # "[TEST] " teaches an author nothing about the email they are building. The create form shows
    # the subject as a PLACEHOLDER rather than a value, so a template reaches the editor with no
    # subject at all and this is easy to do by accident.
    #
    # The test is "did the author WRITE a subject" (the authored string, stripped) — deliberately
    # NOT "does it RENDER non-empty". Matching publish is the point: a canvas publish would accept
    # and mail to real clients must be testable, or the tool for verifying is stricter than the tool
    # for shipping and authors route around it. A subject that merges to nothing (a `{{ token }}`
    # against a context with no value for it) is a MISSING-TOKEN diagnostic, not a refusal:
    # `render_preview` reports it in ``missing``, which this response carries, and refusing it would
    # refuse a template whose subject field is visibly non-empty — an error the author cannot act on
    # from the field it names.
    if not body.subject.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Add a subject line before sending a test. It is what you will see in your inbox."
            ),
        )
    user_id, to = await _own_identity(session)
    # Throttled before anything is rendered or sent: the cap exists to bound Resend spend, and an
    # editor with a held-down "send test" is the exact shape of the accident.
    await ratelimit.enforce(
        ratelimit.EMAIL_TEST,
        ratelimit.email_test_limits(user_id=user_id, tenant_id=tenant_id),
        detail=("Too many test emails. Try again later. You can still use the preview."),
    )
    brand = await email_mod.brand_for(session, tenant_id, portal=portal)
    rendered = _render_or_422(
        subject=body.subject,
        preheader=body.preheader,
        html=body.html,
        css=body.css,
        brand=brand,
        code=code,
        # The caller's own name, because this one lands in their inbox: "Hi Sarah," on an email
        # addressed to you reads as somebody else's mail. `build_preview` passes the same, so the
        # preview an author was just looking at and the test they then send agree.
        viewer_first_name=await _own_first_name(session),
    )
    sender = await email_mod.resolve_sender(session, tenant_id, portal=portal)
    subject = f"{TEST_SUBJECT_PREFIX}{rendered.subject}"
    # Read the guard's own answer BEFORE the send, so a False can be attributed. Both helpers name
    # the setting and never the recipient, which is what makes the text safe to return and log.
    # Refresh first so this answer is the saved list, the same one ``send_email`` is about to use.
    await email_mod.refresh_allowlist()
    reason = email_mod.suppression_reason(to) or email_mod.sending_disabled()
    delivered = await email_mod.send_email(
        to=to,
        subject=subject,
        html=rendered.html,
        sender=sender.from_addr,
        reply_to=sender.reply_to,
        # A test send lands in the AUTHOR's own inbox, so the author is the recipient.
        ctx=ledger.SendContext(
            tenant_id=tenant_id, template_code=code, recipient_user_id=str(user_id)
        ),
    )
    if delivered:
        outcome: Literal["delivered", "suppressed", "failed"] = "delivered"
        detail: str | None = None
    elif reason is not None:
        outcome, detail = "suppressed", "Email sending is unavailable. Contact support."
    else:
        outcome = "failed"
        detail = "The test email could not be sent. Try again later."
    return SendTestEmailOut(
        status=outcome, to=to, subject=subject, detail=detail, missing=rendered.missing
    )


@router.get("", name="list-email-templates")
async def list_email_templates(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[EmailTemplateOut]:
    """The firm's email templates (RLS-scoped), newest first."""
    require_permission(principal, EMAIL_TEMPLATES_READ)
    tenant_id, _ = await _ctx(session)
    rows = await svc.list_templates(session, tenant_id)
    return [EmailTemplateOut(**r) for r in rows]


@router.post("", name="create-email-template", status_code=status.HTTP_201_CREATED)
async def create_email_template(
    body: CreateEmailTemplate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> EmailTemplateOut:
    """Create an email template with an initial empty draft (v1). ``code`` is unique per firm and is
    the stable key the send path resolves (e.g. 'invite')."""
    require_permission(principal, EMAIL_TEMPLATES_WRITE)
    tenant_id, actor_id = await _ctx(session)
    try:
        created = await svc.create_template(
            session,
            tenant_id,
            actor_id,
            code=body.code,
            name=body.name,
            category=body.category,
            description=body.description,
            scope="firm",
            subjects=body.subjects,
        )
    except svc.EmailTemplateError as exc:
        _raise_http(exc)
    return EmailTemplateOut(**created)


@router.get("/catalog", name="email-template-catalog")
async def email_template_catalog(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[EmailCodeOut]:
    """The email ``code``s the send path actually resolves — the selectable "email types" a firm
    picks when creating a template, so a template maps to a real trigger rather than a guessed,
    never-sent code. Declared before ``/{uid}`` so the path param doesn't capture ``catalog``."""
    require_permission(principal, EMAIL_TEMPLATES_READ)
    _ = session  # RLS session kept for a uniform dependency signature; the catalogue is static.
    return [
        EmailCodeOut(code=c.code, label=c.label, category=c.category, description=c.description)
        for c in email_catalog.KNOWN_CODES
    ]


# ── The firm's email image library ────────────────────────────────────────────
# `files/tenant/` is the tenant bucket's ONLY anonymous-read prefix (docs/storage.md §4), and that
# is what decides this whole feature: a mail client fetches an image with no credential of ours,
# hours or weeks after the send. Every other object in the bucket is reachable only through a
# short-lived proxied URL, which is dead by the time the recipient opens the mail — so a
# document-store object is not a lesser email image, it is not an email image at all, and none is
# offered here.

#: The prefix, and therefore the library.
_IMAGE_PREFIX = "files/tenant/"

#: How much of the prefix one listing reads: `_LIBRARY_MAX_PAGES` requests of `_LIBRARY_PAGE` keys.
#: The whole set has to be in hand before it can be ordered by date — see :func:`list_images`.
_LIBRARY_PAGE = 200
_LIBRARY_MAX_PAGES = 5

#: What is OFFERED from the library: the formats a mail client actually draws. `svg` and `ico` are
#: deliberately absent — Outlook and Gmail both refuse SVG, and a favicon is not a picture in an
#: email — so a firm's SVG logo is not on the shelf rather than on it and silently blank.
_LIBRARY_EXTS = imaging.MAILABLE_EXTS

#: What an UPLOAD may be: the two formats the magic-byte sniffer can vouch for
#: (`storage.sniff_document_type` — the one validator in the codebase, and never the declared
#: Content-Type). The asymmetry with the line above is deliberate and worth stating: a GIF already
#: in the bucket is offered, one arriving here is refused, because nothing on this path can prove it
#: is one.
_UPLOAD_EXTS = frozenset({"png", "jpg"})

#: The `kind` an upload is filed under, which becomes its key (`files/tenant/image-{hash}.png`).
#: A distinct word from every branding kind, so an email image can never overwrite a logo — and the
#: keys are content-hashed anyway, so nothing overwrites anything.
_IMAGE_KIND = "image"


def _image_label(key: str) -> str:
    """What a `files/tenant/` object IS, read off its own name — `logo-dark-9f2c.png` → "Logo dark".

    The key is the only record: branding assets are content-hashed on upload and no row anywhere
    remembers what kind they were, so a picker that wants to label a picture has to read the name it
    was given."""
    base = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    kind = base.rsplit("-", 1)[0] if "-" in base else base
    return kind.replace("-", " ").capitalize() or "Image"


class EmailImageOut(BaseModel):
    key: str
    url: str
    label: str
    size: int
    last_modified: str | None = None


class EmailImageLibraryOut(BaseModel):
    images: list[EmailImageOut]
    next_token: str | None = None
    #: Whether this firm has somewhere to PUT an image that a mail client could then fetch. False ⇒
    #: no bucket is resolved, so an upload would fall back to a `data:` URI (`put_branding_asset`'s
    #: last rung) — which mail clients do not render, so the editor says so instead of offering one.
    hosted: bool


class UploadedEmailImageOut(BaseModel):
    key: str
    url: str
    label: str
    size: int
    #: False when `url` is a `data:` URI. The image is stored and usable in the editor, and it will
    #: not appear in an inbox — an outcome the author has to be told, not one to hide.
    hosted: bool


async def _library_key(session: AsyncSession, tenant_id: int, url: str) -> str:
    """The object key `list_images` would publish for `url`, or "" if it is not in that library.

    Both URL shapes resolve: the `/_assets/{name}` path an upload returns now, and the
    object-store URL uploads returned before — a firm's library holds both and the picker has to
    match either against what the listing publishes.
    """
    name = storage.public_asset_name(url)
    if name:
        # The relay's own layout: the anon-read prefix plus the opaque file name.
        return f"{storage.TENANT_PUBLIC_PREFIX}{name}"
    store = await tenant_storage.get_tenant_store(session, tenant_id)
    if store is None or not store.public_base_url:
        return ""
    base = store.public_base_url.rstrip("/") + "/"
    return url[len(base) :] if url.startswith(base) else ""


async def list_images(
    session: AsyncSession, tenant_id: int, token: str | None
) -> EmailImageLibraryOut:
    """A tenant's email image library. Takes the tenant EXPLICITLY so the ops door can read a firm's
    library for an operator curating its templates; the firm door passes its own session tenant, so
    neither can name a bucket the caller has no scope for.

    Newest first — an author's own upload is what they came back for — and DRAINED to make that
    true. S3 lists keys lexicographically, never by date, so sorting a single page orders an
    arbitrary slice convincingly: an author's most recent upload can sit on page two while page one
    presents 200 older images in credible newest-first order, which reads as "the upload did not
    work". Capped, because a bucket has no size limit and this prefix also holds the firm's branding
    assets; past the cap the remaining token is returned rather than dropped, so a caller can go on
    and nothing is silently hidden.
    """
    store = await tenant_storage.get_tenant_store(session, tenant_id)
    # A BUCKET is the whole requirement now, not a bucket with a browser-reachable base URL: the
    # relay is what serves these, and it needs only the object (`proxy=True` on upload).
    if store is None:
        return EmailImageLibraryOut(images=[], next_token=None, hosted=False)
    objects: list[storage.StoredObject] = []
    next_token = token
    try:
        for _ in range(_LIBRARY_MAX_PAGES):
            page = await storage.list_objects(
                store.bucket,
                _IMAGE_PREFIX,
                token=next_token,
                limit=_LIBRARY_PAGE,
                # Flat: `files/tenant/` is a leaf prefix, and a delimiter would report its keys as
                # folders instead of objects.
                delimiter="",
                endpoint=store.endpoint,
                region=store.region,
            )
            objects.extend(page.objects)
            next_token = page.next_token
            if not next_token:
                break
    except (storage.BucketMissing, storage.StorageUnavailable):
        # The registry names a bucket the store does not have (a reset store, a failed provision).
        # An empty library that still reports `hosted` is the honest answer: uploading works again
        # the moment the bucket is repaired, and reporting it unhosted would tell an author to go
        # and find a URL for a firm whose storage is merely broken.
        return EmailImageLibraryOut(images=[], next_token=None, hosted=True)
    images = [
        EmailImageOut(
            key=o.key,
            # The proxy path the upload now returns, derived from the key the same way
            # `put_branding_asset` derives it — so a picture uploaded before this change and one
            # uploaded after are one library with one kind of URL in it.
            url=f"{storage.PUBLIC_ASSET_PREFIX}{o.key.rsplit('/', 1)[-1]}",
            label=_image_label(o.key),
            size=o.size,
            last_modified=o.last_modified,
        )
        for o in objects
        if storage.ext_of(o.key) in _LIBRARY_EXTS
        # A logo's PNG email copy (`storage.put_email_copy`) is the send path's, not a picture
        # anyone uploaded — the shelf stays what the firm put on it.
        and not storage.is_email_copy(o.key.rsplit("/", 1)[-1])
    ]
    images.sort(key=lambda i: i.last_modified or "", reverse=True)
    return EmailImageLibraryOut(images=images, next_token=next_token, hosted=True)


@router.get("/images", name="list-email-template-images")
async def list_email_template_images(
    token: str | None = None,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> EmailImageLibraryOut:
    """The firm's own images, for the editor's picker. Declared before ``/{uid}`` so the path param
    does not capture ``images``.

    Scoped by the SESSION, never by a parameter: the bucket is resolved from ``auth.tenant_id()``
    through the RLS-bound registry read, so there is no argument by which one firm could name
    another's bucket."""
    require_permission(principal, EMAIL_TEMPLATES_READ)
    tenant_id, _ = await _ctx(session)
    return await list_images(session, tenant_id, token)


class _ImageUploadRoute(CappedBodyRoute):
    """This one route's body ceiling, applied at the ASGI-receive layer — the only layer that sees
    the wire bytes. Starlette spools a file part to DISK with no limit of its own, and it does so
    before this module's handler or its dependencies run, so a cap inside the handler is already too
    late to bound what an oversized body costs. One asset plus multipart framing."""

    body_limit: int = storage.MAX_ASSET_BYTES + 8 * 1024


async def store_image(
    session: AsyncSession, tenant_id: int, file: UploadFile
) -> UploadedEmailImageOut:
    """Put one image into a tenant's library and describe where it landed. Tenant EXPLICIT, for the
    reason ``list_images`` gives.

    The bytes are magic-byte sniffed by ``storage.validate_upload``; the declared Content-Type is
    never trusted and never consulted."""
    try:
        data = await storage.read_capped(file, storage.MAX_ASSET_BYTES)
    except storage.PayloadTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"An email image must be 1..{storage.MAX_ASSET_BYTES} bytes.",
        ) from exc
    content_type = ""
    try:
        content_type, ext = storage.validate_upload(data)
    except ValueError:
        # The sniffer's own refusals name the DOCUMENT rule it enforces ("zip content is only
        # accepted as a .docx document"), which answers a question nobody here asked. Our rule is
        # narrower than its allowlist, so one message states it for every rejection.
        ext = ""
    if ext not in _UPLOAD_EXTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An email image must be a PNG or JPEG file.",
        )
    try:
        # `proxy=True`, as branding and CMS uploads already do. An email image was the ONE asset
        # still published as its object-store URL, and that origin is internal: the author saw a
        # broken picture in the canvas wherever the browser could not reach the store, and the
        # bytes only reached an inbox because the send path inlines them (`email/images.py`) —
        # within a four-image budget, past which the URL travelled as-is and arrived broken. The
        # relay serves it to both (`routers/internal.py::public_asset`), and the send path makes
        # the path absolute against the firm's own host (`email/document.py::to_email_html`).
        url = await storage.put_branding_asset(
            session, tenant_id, _IMAGE_KIND, data, content_type, proxy=True
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    # A `data:` URI is still the last rung of `put_branding_asset` (no bucket at all), and it is
    # still the one outcome an author has to be told about: it renders in the editor and in no
    # inbox. Everything else — the proxy path included — is hosted.
    hosted = not url.startswith("data:")
    return UploadedEmailImageOut(
        # The SAME identity `list_images` publishes — the full object key, derived by stripping the
        # store's public base off the URL it just built from it. A bare filename here named a
        # different thing from the listing's `o.key` for the same object, so the two halves of one
        # library could not be matched up. Blank when the URL is not under this store's base: a
        # `data:` URI, or the legacy shared bucket, neither of which the listing can read.
        key=await _library_key(session, tenant_id, url) if hosted else "",
        url=url,
        label=_image_label(url) if hosted else "Image",
        size=len(data),
        hosted=hosted,
    )


async def upload_email_template_image(
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> UploadedEmailImageOut:
    """Add an image to the firm's library and return its URL.

    Gated on ``email_templates:write`` rather than ``branding:write``: the object lands in the same
    public prefix, but the act is putting content into an email, and the two permissions are held by
    different people — an author who may edit the firm's emails must not thereby be able to change
    the logo on its login page (nor, since keys are content-hashed under a distinct ``image-`` name,
    can this overwrite one)."""
    require_permission(principal, EMAIL_TEMPLATES_WRITE)
    tenant_id, _ = await _ctx(session)
    return await store_image(session, tenant_id, file)


# Registered imperatively, not with @router.post, purely to give this ONE route the asset ceiling —
# the decorator form has no way to pass a route class, and the allowance must not leak onto the
# template-save routes, whose bodies are legitimately larger. In source order, before ``/{uid}``.
router.add_api_route(
    "/images",
    upload_email_template_image,
    methods=["POST"],
    name="upload-email-template-image",
    status_code=status.HTTP_201_CREATED,
    route_class_override=_ImageUploadRoute,
)


@router.get("/{uid}", name="get-email-template")
async def get_email_template(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> EmailTemplateOut:
    """One email template's metadata (RLS-scoped)."""
    require_permission(principal, EMAIL_TEMPLATES_READ)
    tenant_id, _ = await _ctx(session)
    template = await svc.fetch(session, tenant_id, uid)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Email template not found"
        )
    return EmailTemplateOut(**template)


@router.post("/{uid}/preview", name="preview-email-template")
async def preview_email_template(
    uid: str,
    body: PreviewEmail,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> PreviewEmailOut:
    """Render the editor's CURRENT content against representative sample values, folded inline for
    email clients — WITHOUT sending. The author sees exactly what the recipient would receive, in
    the firm's real brand (name/logo/accent).

    ``audience`` picks WHICH of the firm's two brands dresses it — the client-portal one (default)
    or the staff/platform one — because the recipient's audience is a send-time fact the template
    itself doesn't carry."""
    require_permission(principal, EMAIL_TEMPLATES_READ)
    tenant_id, _ = await _ctx(session)
    template = await svc.fetch(session, tenant_id, uid)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Email template not found"
        )
    return await build_preview(session, tenant_id, str(template["code"]), body)


@router.post("/{uid}/send-test", name="send-test-email-template")
async def send_test_email_template(
    uid: str,
    body: SendTestEmail,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> SendTestEmailOut:
    """Mail the editor's CURRENT content to YOUR OWN address — the inbox test the browser preview
    cannot be. A rendered preview shows the HTML a modern browser makes of it; it says nothing about
    Outlook's table handling, dark-mode inversion, a client that blocks images, whether the
    preheader actually surfaces, or whether the CTA is tappable on a phone. Those only show up in a
    real inbox.

    **The recipient is you, and only you.** It is resolved from your session (``auth.users.email``);
    the request body carries no address and any address in it is ignored. Gated on
    ``email_templates:write`` — the author's permission — because this spends the firm's real email
    quota and puts mail on the wire, which a read-only viewer of the templates should not do.

    Marked ``[TEST]`` in the subject so a test landing in a real inbox cannot be mistaken for a live
    send, and throttled per author (``app/ratelimit.py``). Answers ``delivered`` / ``suppressed``
    (this deployment mails nobody — the reason names the setting) / ``failed``, so a suppressed send
    is never reported as a success.

    **422 on a blank subject**, the same gate publish applies: a test send that arrives as a bare
    ``[TEST] `` teaches nothing about the inbox it exists to show you. Preview has no such gate —
    it sends nothing, so there is no inbox to be wrong about. A subject that merges to nothing comes
    back in ``missing`` instead (a diagnostic, not a refusal)."""
    require_permission(principal, EMAIL_TEMPLATES_WRITE)
    tenant_id, _ = await _ctx(session)
    template = await svc.fetch(session, tenant_id, uid)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Email template not found"
        )
    return await send_test(session, tenant_id, str(template["code"]), body)


@router.get("/{uid}/default", name="email-template-default")
async def email_template_default(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> EmailDefaultOut:
    """The built-in default for this template's ``code``, rendered with ``{{ brand.* }}`` / client /
    document merge tokens, for seeding a blank editor canvas. 404 if the template's code has no
    shipped default (e.g. a custom code) — the UI hides "Load default" in that case."""
    require_permission(principal, EMAIL_TEMPLATES_READ)
    tenant_id, _ = await _ctx(session)
    template = await svc.fetch(session, tenant_id, uid)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Email template not found"
        )
    default = email_defaults.default_for(str(template["code"]))
    if default is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No built-in default for this template's code.",
        )
    return EmailDefaultOut(
        subject=default.subject,
        preheader=default.preheader,
        html=default.html,
        css=default.css,
        source=default.source,
    )


@router.patch("/{uid}", name="update-email-template")
async def update_email_template(
    uid: str,
    body: UpdateEmailTemplate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> EmailTemplateOut:
    """Update an email template's metadata. Partial: an absent key is left alone.

    ``description`` is clearable — send it as ``""`` (or null) to delete it, omit it to keep it. It
    is the only field here that can be emptied, and a COALESCE cannot say the difference."""
    require_permission(principal, EMAIL_TEMPLATES_WRITE)
    tenant_id, _ = await _ctx(session)
    try:
        updated = await svc.update_template(
            session,
            tenant_id,
            uid,
            name=body.name,
            code=body.code,
            category=body.category,
            description=body.description,
            description_set="description" in body.model_fields_set,
        )
    except svc.EmailTemplateError as exc:
        _raise_http(exc)
    return EmailTemplateOut(**updated)


@router.delete("/{uid}", name="delete-email-template")
async def delete_email_template(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> EmailTemplateOut:
    """Delete an email template and its version history. Irreversible.

    Answers with the row it destroyed, so the surface can name what went. 404 if it is not there,
    and no other refusal: what still names the code is put to the author in the confirmation, which
    lists the notification rules that render it, rather than blocking the delete on their behalf.
    """
    require_permission(principal, EMAIL_TEMPLATES_WRITE)
    tenant_id, _ = await _ctx(session)
    try:
        template = await svc.delete_template(session, tenant_id, uid)
    except svc.EmailTemplateError as exc:
        _raise_http(exc)
    return EmailTemplateOut(**template)


@router.get("/{uid}/versions", name="list-email-template-versions")
async def list_email_template_versions(
    uid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> list[EmailVersionOut]:
    """A template's version history, newest first."""
    require_permission(principal, EMAIL_TEMPLATES_READ)
    tenant_id, _ = await _ctx(session)
    try:
        rows = await svc.list_versions(session, tenant_id, uid)
    except svc.EmailTemplateError as exc:
        _raise_http(exc)
    return [EmailVersionOut(**r) for r in rows]


@router.get("/{uid}/versions/{vid}", name="get-email-template-version")
async def get_email_template_version(
    uid: str,
    vid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> EmailVersionDetailOut:
    """One version, including its editor content (``schema``)."""
    require_permission(principal, EMAIL_TEMPLATES_READ)
    tenant_id, _ = await _ctx(session)
    try:
        row = await svc.get_version(session, tenant_id, uid, vid)
    except svc.EmailTemplateError as exc:
        _raise_http(exc)
    return EmailVersionDetailOut(**row)


@router.post("/{uid}/versions", name="save-email-template-version")
async def save_email_template_version(
    uid: str,
    body: SaveEmailVersion,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> EmailVersionDetailOut:
    """Save the editor's content (the editor autosaves here). Updates the head draft in place while
    it is a fresh draft; rolls to a new draft ``v(n+1)`` once the head is published or its 30-minute
    bucket elapses — leaving the previous draft as a restore point."""
    require_permission(principal, EMAIL_TEMPLATES_WRITE)
    tenant_id, actor_id = await _ctx(session)
    try:
        row = await svc.save_version(
            session,
            tenant_id,
            actor_id,
            uid,
            subject=body.subject,
            preheader=body.preheader,
            schema=body.schema_content,
            scope="firm",
        )
    except svc.EmailTemplateError as exc:
        _raise_http(exc)
    return EmailVersionDetailOut(**row)


@router.post("/{uid}/versions/{vid}/publish", name="publish-email-template-version")
async def publish_email_template_version(
    uid: str,
    vid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> PublishEmailResultOut:
    """Publish a draft: require a subject, a visible body and no leftover editor placeholder copy
    (422 naming the offending text), archive the prior published version, mark this published, and
    point the template at it (one active version at a time)."""
    require_permission(principal, EMAIL_TEMPLATES_WRITE)
    tenant_id, actor_id = await _ctx(session)
    try:
        row = await svc.publish_version(session, tenant_id, actor_id, uid, vid, scope="firm")
    except svc.EmailTemplateError as exc:
        _raise_http(exc)
    return PublishEmailResultOut(**row)


@router.post("/{uid}/versions/{vid}/restore", name="restore-email-template-version")
async def restore_email_template_version(
    uid: str,
    vid: str,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> EmailVersionDetailOut:
    """Copy an earlier version's content into a NEW draft — the roll-back path. Never overwrites and
    never auto-publishes: the restored content lands as a fresh draft ``v(n+1)`` the author reviews
    and publishes as usual (so a mis-click can itself be rolled back)."""
    require_permission(principal, EMAIL_TEMPLATES_WRITE)
    tenant_id, actor_id = await _ctx(session)
    try:
        row = await svc.restore_version(session, tenant_id, actor_id, uid, vid, scope="firm")
    except svc.EmailTemplateError as exc:
        _raise_http(exc)
    return EmailVersionDetailOut(**row)
