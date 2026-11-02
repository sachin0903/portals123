"""Transactional email via Resend (self-hosted onboarding + client notifications).

The API sends invites, password-set/verification emails and the client-facing document notices
itself so the portals never bounce to AuthKit's hosted pages (white-label). ``resolve_sender``
turns a tenant into the envelope identity its mail goes out as, and ``brand_for`` dresses the
templates in the firm's own name/logo/colour from ``auth.tenants.theme`` — so a firm's client sees
their adviser's brand, not Graphene's. A firm with a verified row in ``email.sender_domains``
sends from the address operations chose on that domain; every other firm keeps ``EMAIL_FROM``.

Email is best-effort: if Resend isn't configured or a send fails we log and return False rather
than failing the calling request — the set-password link is also surfaced in the admin UI to
copy/share manually, and the notification outbox retries its own sends.

**``send_email`` is the single chokepoint, and the non-production send guard lives inside it**
(``suppression_reason``). Nothing else in the API holds the Resend credential or names Resend's
endpoint — pinned by ``tests/test_email_send_guard.py``, because a second sender would be a second
route to a real client's inbox from a staging box. See that module's docstring for the rule.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import quote

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import branding as branding_mod
from app import imaging, storage
from app.comms import ledger
from app.config import settings
from app.email import images as email_images

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
RESEND_DOMAINS = "https://api.resend.com/domains"

#: Where a tenant domain sends from. Closest region to the firms this platform serves.
SENDING_REGION = "eu-west-1"

# A resolved brand/sender is reused this long — a slightly stale logo beats a round trip per email.
CACHE_TTL_SECONDS = 300.0


class ResendError(Exception):
    """Resend refused a domain call, or could not be reached.

    ``status_code`` is the status we answer the operator with. It is never Resend's own: a 422
    from Resend is a domain we will not register, and a 5xx from Resend is our 502.
    """

    def __init__(self, message: str, *, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(message)


class ResendDomain(NamedTuple):
    """A sending domain as Resend described it. ``records`` are the DNS rows the operator copies."""

    id: str
    name: str
    status: str
    region: str
    records: list[dict[str, object]]


def _resend_message(resp: httpx.Response) -> str:
    """The message Resend sent back, bounded, or a fixed sentence when there isn't one."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON error body is still a refusal
        return "Resend refused the domain."
    if isinstance(body, dict):
        message = body.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()[:300]
    return "Resend refused the domain."


def _domain_record(raw: object) -> dict[str, object] | None:
    """One DNS record, keeping only the fields the settings page shows."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    record_type = raw.get("type")
    value = raw.get("value")
    if not isinstance(name, str) or not isinstance(record_type, str) or not isinstance(value, str):
        return None
    record: dict[str, object] = {
        "record": raw.get("record") if isinstance(raw.get("record"), str) else "",
        "name": name,
        "type": record_type,
        "value": value,
        "status": raw.get("status") if isinstance(raw.get("status"), str) else "not_started",
    }
    ttl = raw.get("ttl")
    if isinstance(ttl, str) and ttl:
        record["ttl"] = ttl
    priority = raw.get("priority")
    if isinstance(priority, int):
        record["priority"] = priority
    return record


def _parse_domain(body: object) -> ResendDomain:
    if not isinstance(body, dict):
        raise ResendError("Resend returned an unexpected domain response.", status_code=502)
    domain_id = body.get("id")
    name = body.get("name")
    if not isinstance(domain_id, str) or not domain_id or not isinstance(name, str) or not name:
        raise ResendError("Resend returned an unexpected domain response.", status_code=502)
    status_value = body.get("status")
    region = body.get("region")
    records: list[dict[str, object]] = []
    raw_records = body.get("records")
    if isinstance(raw_records, list):
        for item in raw_records:
            parsed = _domain_record(item)
            if parsed is not None:
                records.append(parsed)
    return ResendDomain(
        id=domain_id,
        name=name.strip().lower().rstrip("."),
        status=status_value if isinstance(status_value, str) and status_value else "not_started",
        region=region if isinstance(region, str) and region else SENDING_REGION,
        records=records,
    )


async def _resend_json(method: str, url: str, body: dict[str, object] | None = None) -> object:
    """One authenticated call to Resend's domain API. The credential stays in this module."""
    if not settings.resend_api_key:
        raise ResendError("RESEND_API_KEY is not set", status_code=503)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.HTTPError as exc:
        raise ResendError("Resend could not be reached.", status_code=503) from exc
    if resp.status_code == 404:
        raise ResendError("Resend does not have this domain.", status_code=404)
    if resp.status_code >= 400:
        code = 422 if resp.status_code < 500 else 502
        raise ResendError(_resend_message(resp), status_code=code)
    if resp.status_code == 204 or not resp.content:
        return None
    try:
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - a success body we cannot parse is still a failure
        raise ResendError(
            "Resend returned an unexpected domain response.", status_code=502
        ) from exc


async def create_sending_domain(name: str) -> ResendDomain:
    """Register a sending-only domain in the EU region. Receiving and tracking stay off."""
    body = await _resend_json(
        "POST",
        RESEND_DOMAINS,
        {
            "name": name,
            "region": SENDING_REGION,
            "open_tracking": False,
            "click_tracking": False,
            "capabilities": {"sending": "enabled", "receiving": "disabled"},
        },
    )
    return _parse_domain(body)


async def verify_sending_domain(domain_id: str) -> None:
    """Ask Resend to check the domain's DNS. The result arrives later, on the next retrieve."""
    await _resend_json("POST", f"{RESEND_DOMAINS}/{quote(domain_id, safe='')}/verify")


async def retrieve_sending_domain(domain_id: str) -> ResendDomain:
    body = await _resend_json("GET", f"{RESEND_DOMAINS}/{quote(domain_id, safe='')}")
    return _parse_domain(body)


async def delete_sending_domain(domain_id: str) -> None:
    """Remove the domain from Resend. A 404 means it is already gone, which is the same outcome."""
    try:
        await _resend_json("DELETE", f"{RESEND_DOMAINS}/{quote(domain_id, safe='')}")
    except ResendError as exc:
        if exc.status_code != 404:
            raise


class Brand(NamedTuple):
    """The firm identity an email is dressed in, resolved from ``auth.tenants`` (name + theme).

    ``logo_url`` is only ever an absolute URL to a picture a mail client draws inline — never a
    ``data:`` image (mail clients block them) and never an SVG, WebP or ICO (Outlook lists those as
    a file attachment, and SVG is also broken in the body). A logo stored in one of those types is
    represented by its PNG email copy (``email_assets``); with none, the masthead falls back to the
    other image, then to the wordmark. ``accent`` is only ever a hex colour, because email clients
    don't understand the design system's ``oklch()`` tokens. Defaults are Graphene's own."""

    name: str = "Graphene"
    #: The masthead image, resolved from the firm's treatment (``icon`` yields the icon).
    logo_url: str | None = None
    accent: str = "#0f172a"
    support_email: str | None = None
    #: The firm's MARK alone (https-only), so a template can place a badge a resolved logo cannot.
    icon_url: str | None = None


GRAPHENE_BRAND = Brand()


class Sender(NamedTuple):
    """Envelope identity for one tenant's mail: the ``From`` header, plus an optional ``Reply-To``
    when the firm's own address can't be used as the From (see ``resolve_sender``)."""

    from_addr: str
    reply_to: str | None = None


class OnBehalfOf(NamedTuple):
    """A person a send goes out as, rather than the firm — today an adviser mailing their own
    client.

    Both halves are optional in practice: an adviser record may carry a name and no address, and the
    resolution below degrades rather than refusing, because a notice that arrives from the firm is
    always better than one that does not arrive.
    """

    name: str
    email: str | None = None


_brand_cache: dict[tuple[int, str], tuple[float, Brand]] = {}
_sender_cache: dict[tuple[int, str], tuple[float, Sender]] = {}


def clear_caches() -> None:
    """Drop the resolved brand/sender/image caches (tests, and after a branding save)."""
    _brand_cache.clear()
    _sender_cache.clear()
    _email_assets.clear()


def _address(mailbox: str) -> str:
    """The bare addr-spec of a ``Name <addr@host>`` mailbox (or the string as-is).

    Lossy by construction — it takes the FIRST ``<…>`` pair and discards everything else — so it is
    only safe on a value ``_single_address`` has already accepted. Do not reach for it to derive a
    recipient; see that function."""
    if "<" in mailbox and ">" in mailbox:
        return mailbox.split("<", 1)[1].split(">", 1)[0].strip()
    return mailbox.strip()


#: In a ``to`` these mean more than one mailbox, or header injection; a quoted comma is refused too.
_MULTI_ADDRESS_CHARS = (",", ";", "\n", "\r")

#: …and what cannot be in the addr-spec itself: a residue means it was never one mailbox.
_NOT_IN_AN_ADDRESS = (" ", "\t", "<", ">", '"')


def _single_address(to: str) -> str | None:
    """The ONE address ``to`` names, normalised — or None when it does not name exactly one.

    **This is what makes the non-production guard sound.** ``send_email`` posts THIS value, and
    ``recipient_allowed`` checks THIS value, so the address compared against ``EMAIL_ALLOWLIST`` and
    the address Resend is handed are the same string by construction rather than by two derivations
    happening to agree. They did not agree before: the guard normalised (``_address`` then
    ``rpartition('@')``) while the payload posted the raw original, and ``rpartition`` leaves
    everything before the LAST ``@`` unexamined — so ``victim@evil.com, ok@allowed.test`` was
    checked as domain ``allowed.test`` and then sent, verbatim, to both.

    That is reachable, not theoretical: ``to`` comes from ``crm.contacts.email``, which has no shape
    validation at all (``app/contacts.py`` takes ``email: str | None`` straight into the INSERT).

    So a multi-recipient string is refused outright rather than parsed. Every call site in the app
    mails ONE person — a document notice, an invite, a task notice, a flow action — so there is no
    legitimate value this rejects, and a permissive reading of one is the whole exploit."""
    raw = to.strip()
    if not raw or any(ch in raw for ch in _MULTI_ADDRESS_CHARS) or raw.count("@") != 1:
        return None
    addr = _address(raw)
    if not addr or any(ch in addr for ch in _NOT_IN_AN_ADDRESS):
        return None
    local, _, domain = addr.partition("@")
    if not local or not domain:
        return None
    return addr


def _domain(addr: str) -> str:
    return addr.rpartition("@")[2].strip().lower()


def _mailbox(display_name: str, addr: str) -> str:
    """A ``Name <addr>`` header value. The display name is quoted-string-escaped so a firm name
    with a comma/quote can't split the header into two addresses."""
    clean = display_name.replace("\\", "").replace('"', "").replace("\n", " ").strip()
    return f'"{clean}" <{addr}>' if clean else addr


async def _tenant_row(
    session: AsyncSession, tenant_id: int
) -> tuple[str, dict[str, object]] | None:
    row = (
        (
            await session.execute(
                text("SELECT name, theme FROM auth.tenants WHERE id = :t"), {"t": tenant_id}
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    theme = row["theme"] if isinstance(row["theme"], dict) else {}
    return str(row["name"]), theme


def brand_tokens(brand: Brand) -> dict[str, str | None]:
    """The nested ``brand`` value a send site puts in the merge context — and the only shape the
    templates' ``{{ brand.* }}`` tokens are written against.

    One definition because there were seven: every ``render_or_default`` call site spelled this dict
    out by hand, which is a per-site opportunity to omit a key (and a key that renders blank in an
    email is invisible until a client asks who wrote to them). ``email_preview.sample_context``
    builds the same shape from the firm's real brand, so preview and send agree by construction."""
    return {
        "name": brand.name,
        "logo_url": brand.logo_url,
        "accent": brand.accent,
        "support_email": brand.support_email,
        # The mark alone — a firm with an icon and no wordmark had no way to get an image in.
        "icon_url": brand.icon_url,
    }


@dataclass(frozen=True)
class EmailAsset:
    """The bytes an email carries for one stored asset — always a type a mail client draws
    inline."""

    data: bytes
    content_type: str
    #: The stored asset these exact bytes are — the original, or its email copy — so a URL to it
    #: shows the same picture if the bytes do not travel. None when they were made in memory.
    stored_as: str | None


#: Resolved email assets, keyed ``(tenant_id, name)``. An answer is reused for the life of the
#: process: names are content-hashed, so the bytes behind one never change. A MISS is reused only
#: for ``CACHE_TTL_SECONDS`` — the copy an SVG lacks today is written by the next upload of the
#: same file, under the same name. Bounded, oldest out first: every entry is a logo's bytes.
_email_assets: OrderedDict[tuple[int, str], tuple[float, EmailAsset | None]] = OrderedDict()
_EMAIL_ASSETS_MAX = 64


def _cached_email_asset(tenant_id: int, name: str) -> tuple[bool, EmailAsset | None]:
    """``(hit, asset)`` from the cache alone."""
    key = (tenant_id, name)
    hit = _email_assets.get(key)
    if hit is None:
        return False, None
    stamped, asset = hit
    if asset is None and time.monotonic() - stamped >= CACHE_TTL_SECONDS:
        del _email_assets[key]
        return False, None
    _email_assets.move_to_end(key)
    return True, asset


def _remember_email_asset(tenant_id: int, name: str, asset: EmailAsset | None) -> None:
    _email_assets[(tenant_id, name)] = (time.monotonic(), asset)
    _email_assets.move_to_end((tenant_id, name))
    while len(_email_assets) > _EMAIL_ASSETS_MAX:
        _email_assets.popitem(last=False)


async def _resolve_email_asset(
    session: AsyncSession, tenant_id: int, name: str
) -> EmailAsset | None:
    """What an email carries for the asset ``name``, or None when it can carry nothing for it.

    1. A PNG, JPEG or GIF travels as it is — judged by its BYTES, not its name.
    2. Anything else travels as its email copy (``storage.email_copy_name``), stored at upload.
    3. With no copy — an upload from before copies existed — it is converted here, in memory
       (CairoSVG draws an SVG), and the result is stored as the copy, so the next process reads it
       instead of converting again. Where an SVG cannot be drawn (no Cairo library) it is None.

    A missing or refused name is None. An OUTAGE raises, so the caller can tell "this asset has no
    picture" (cached) from "storage did not answer" (not cached)."""
    original: bytes | None = None
    if imaging.is_mailable(name):
        try:
            data, _ = await storage.get_public_asset(session, tenant_id, name)
        except (ValueError, FileNotFoundError):
            return None
        content_type = imaging.mailable_type(data)
        if content_type is not None:
            return EmailAsset(data, content_type, name)
        # A `.png`/`.jpg` NAME over bytes of another type — a WebP saved from a website as
        # `logo.png` keeps the name the browser gave it. Handled like any other type below.
        original = data
    copy = storage.email_copy_name(name)
    if copy is None:
        return None
    try:
        data, _ = await storage.get_public_asset(session, tenant_id, copy)
        if imaging.mailable_type(data) == "image/png":
            return EmailAsset(data, "image/png", copy)
    except FileNotFoundError:
        # No stored copy: a logo uploaded before copies existed. Step 3 draws one below.
        logger.debug("no email copy of %s yet; converting the original", name)
    if original is None:
        try:
            original, _ = await storage.get_public_asset(session, tenant_id, name)
        except (ValueError, FileNotFoundError):
            return None
    png = await asyncio.to_thread(imaging.email_png, original)
    if png is None:
        return None
    stored: str | None = None
    try:
        stored = await storage.put_email_copy(session, tenant_id, name, png)
    except Exception:
        logger.warning("email copy of %s could not be stored", name, exc_info=True)
    return EmailAsset(png, "image/png", stored)


async def email_assets(tenant_id: int | None, names: Sequence[str]) -> dict[str, EmailAsset]:
    """What an email carries for each of ``names`` (stored asset names), keyed by name; a name it
    can carry nothing for is absent.

    THE SAME READ THE PORTAL'S RELAY DOES (`storage.get_public_asset`), and deliberately not an
    HTTP fetch of the portal: the API may have no route to the public portal host, on a dev box it
    certainly has none over https, and a request to a URL that appears in author-supplied markup is
    a question this avoids having to answer at all. The recipient needs no route to the firm either
    — that is what a content id buys.

    ONE short-lived `service_role` transaction, opened here rather than threaded through the
    callers, and only when something is not already cached: this is the same posture
    `comms/ledger.record` takes, and for the same reason — the work belongs to the send, not to
    whatever request happened to trigger it, and the worker's send paths have no session to lend.

    Never raises and never blocks a send: a storage or database outage returns what is already in
    hand, and the rest travel as URLs — the behaviour that shipped before images were inlined."""
    if tenant_id is None or not names:
        return {}
    resolved: dict[str, EmailAsset] = {}
    missing: list[str] = []
    for name in dict.fromkeys(names):
        hit, asset = _cached_email_asset(int(tenant_id), name)
        if not hit:
            missing.append(name)
        elif asset is not None:
            resolved[name] = asset
    if not missing:
        return resolved
    try:
        # Imported here, not at module scope: `app.db` opens the engine, and this module is imported
        # by the pure `email.shell` chain (see its docstring) which must stay free of it.
        from app.db import system_transaction

        async with system_transaction() as session:
            for name in missing:
                asset = await _resolve_email_asset(session, int(tenant_id), name)
                _remember_email_asset(int(tenant_id), name, asset)
                if asset is not None:
                    resolved[name] = asset
    except Exception:
        # A storage or database outage must not stop the mail. The images travel as URLs, which is
        # exactly the behaviour that shipped before they were inlined at all.
        logger.warning("email assets could not be read — images left as URLs", exc_info=False)
    return resolved


async def _own_assets(html: str, tenant_id: int | None) -> dict[str, tuple[bytes, str]]:
    """The bytes of every relay asset this email names, ready to travel with it
    (``email_assets``).

    Never raises and never blocks a send. An asset that cannot be read is simply absent from the
    map, and the inliner leaves its URL alone — the floor this whole module keeps.
    """
    if tenant_id is None:
        return {}
    names = email_images.relay_asset_names(html)[: email_images.MAX_INLINE_IMAGES]
    assets = await email_assets(tenant_id, names)
    return {name: (asset.data, asset.content_type) for name, asset in assets.items()}


def _https_only(url: str | None) -> str | None:
    """An image URL a mail client will actually fetch AND draw, or None.

    Branding accepts a ``data:`` URI (the portals render it inline happily), and mail clients block
    those — so an inlined image is not a usable masthead and must fall through to the next option
    rather than arriving as a broken picture. The same for an https URL naming an SVG, WebP or
    other type a mail client does not draw inline: a firm can type one in, and it has no email
    copy. A URL with no extension at all is kept, as it always was."""
    if not (url or "").startswith("https://"):
        return None
    return None if imaging.is_known_unmailable(url or "") else url


def masthead_image(branding: branding_mod.Branding) -> str | None:
    """The image an email's masthead should wear, or None to wear the text wordmark instead.

    THREE THINGS WERE WRONG BEFORE THIS, all of them the same omission — it read ``logo_url`` alone:

      * ``logo_variant`` was ignored. A firm that chose the ICON treatment for its portals got its
        full wordmark lockup in email, which is the one place a lockup is most likely to be too
        wide.
      * ``icon_url`` was never read at all, so a firm that had uploaded a mark and no wordmark got
      the
        TEXT wordmark — the fallback for a firm with no brand imagery — while holding brand imagery.
      * A ``data:`` logo fell straight to the text wordmark with no second chance at the icon.

    So the firm's own stated treatment decides, and the other image is the fallback: an author who
    picked one and uploaded the other still gets a picture. ``logo_dark_url`` is deliberately NOT
    considered — mail clients have no reliable way to swap an image by colour scheme, so a dark-mode
    logo in an email is a coin toss, and the light one is the safe single answer."""
    icon = _https_only(branding.icon_url)
    wordmark = _https_only(branding.logo_url)
    return (icon or wordmark) if branding.logo_variant == "icon" else (wordmark or icon)


async def _portal_hosts(session: AsyncSession, tenant_id: int) -> tuple[str | None, str | None]:
    """``(client_portal_host, platform_host)`` for a tenant, or ``(None, None)``."""
    row = (
        (
            await session.execute(
                text(
                    "SELECT client_portal_host, platform_host "
                    "FROM auth.tenant_environments WHERE tenant_id = :t LIMIT 1"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None, None
    # `.get`, not `[...]`: an absent column is an unregistered host, which is a real state this
    # resolver already has an answer for (None → the masthead wears type). A KeyError here would
    # take down a send over branding, which is the one thing every path in this module refuses to
    # do — see `brand_for`'s handling of an invalid theme.
    return row.get("client_portal_host"), row.get("platform_host")


#: Hosts that are this machine. A registered dev host is one of these with a port
#: (`portals2.localhost:5183`), and the dev servers speak http — there is no TLS listener to reach.
_LOOPBACK = ("localhost", "127.0.0.1", "[::1]", "::1")


def _as_origin(host: object) -> str | None:
    """A registered host as an origin an image can be fetched from, or None when it is not set.

    HTTPS EVERYWHERE EXCEPT THIS MACHINE. A real portal host is https and must be stated as such —
    many mail clients refuse to load an http image, and a firm's logo arriving over plain http is
    worse than not arriving. But a DEV host is `portals2.localhost:5183`, where nothing is listening
    on 443: forcing the scheme there produced an absolute URL that could never load, so the masthead
    went from "the firm's name in type" (correct, and readable) to a broken-image glyph with the
    alt text beside it (wrong, and looks like the feature is broken). That is a worse failure than
    the one this whole path exists to fix, and it is the state every dev box was left in.

    Matched on the HOST, not on the stage: a deployment's `APP_STAGE` says what it is FOR, and this
    question is only ever "can a browser reach 443 here". A `.localhost` suffix is reserved for
    exactly this (RFC 6761) and is what the dev hosts use.
    """
    if not host:
        return None
    value = str(host).rstrip("/")
    if value.startswith(("https://", "http://")):
        return value
    name = value.split(":", 1)[0].lower()
    local = name in _LOOPBACK or name.endswith(".localhost")
    return f"{'http' if local else 'https'}://{value}"


async def asset_origin(session: AsyncSession, tenant_id: int | None) -> str | None:
    """An origin that can serve THIS TENANT'S public assets to a mail client, or None.

    For the images inside an email's BODY — an author's own picture, dropped into their template in
    the editor and stored as a `/_assets/{name}` path like every other asset in the product. A path
    is meaningless in an inbox, so the send path states a host (`storage.absolute_asset_urls`).

    EITHER PORTAL WILL DO, which is why this takes no `portal` argument where `brand_for` does. The
    relay resolves the tenant from the host it was called on and then reads that tenant's bucket
    (`routers/internal.py::public_asset` → `storage.get_public_asset`), so both of a firm's hosts
    serve the same objects. The client host is preferred only because it is the one a firm is
    likeliest to have registered and the one its clients already see in links.

    None when the firm has no host registered at all: there is nothing to point an image at, and the
    body keeps the path it was authored with rather than gaining a URL that resolves nowhere.
    """
    if tenant_id is None:
        return None
    client_host, platform_host = await _portal_hosts(session, int(tenant_id))
    return _as_origin(client_host) or _as_origin(platform_host)


async def _email_asset_urls(
    session: AsyncSession,
    tenant_id: int,
    portal: branding_mod.Portal,
    branding: branding_mod.Branding,
) -> tuple[str | None, str | None]:
    """Resolve the chosen masthead and icon without ever publishing the S3 origin.

    A stored logo or icon in a type a mail client does not draw inline (SVG, WebP, ICO) is stated
    as its PNG email copy when one is stored, kept as itself when ``email_assets`` made the copy in
    memory (the send attaches those bytes), and dropped when there is no picture at all — so the
    other image, or the wordmark, takes its place rather than a broken image and an attachment."""
    values = (branding.logo_url, branding.icon_url)
    names = [n for v in values if (n := storage.public_asset_name(v or ""))]
    needs_origin = bool(names)
    # EITHER of the firm's hosts, not this portal's alone. A stored logo is a `/_assets/{name}`
    # path, so it needs a host to be stated against — and the relay resolves the TENANT from
    # whichever host it was called on and then reads that tenant's bucket
    # (`routers/internal.py::public_asset`), so both of a firm's hosts serve the same object.
    # Insisting on the sending portal's own host meant a firm with a client portal and no platform
    # host had its staff mail fall back to the TEXT WORDMARK while holding a perfectly servable
    # logo — the same shape of omission `masthead_image` above was written to fix, one layer down.
    origin = await asset_origin(session, tenant_id) if needs_origin else None
    unmailable = [n for n in names if not imaging.is_mailable(n)]
    ready = await email_assets(tenant_id, unmailable) if unmailable and origin else {}

    def resolve(value: str | None) -> str | None:
        if _https_only(value):
            return value
        name = storage.public_asset_name(value or "")
        if not name or not origin:
            return None
        if not imaging.is_mailable(name):
            asset = ready.get(name)
            if asset is None:
                return None
            name = asset.stored_as or name
        return f"{origin}{storage.PUBLIC_ASSET_PREFIX}{name}"

    icon = resolve(branding.icon_url)
    wordmark = resolve(branding.logo_url)
    masthead = (icon or wordmark) if branding.logo_variant == "icon" else (wordmark or icon)
    return masthead, icon


async def brand_for(
    session: AsyncSession, tenant_id: int | None, *, portal: branding_mod.Portal = "client"
) -> Brand:
    """The firm's email branding (name, logo, accent, support address) for one portal's audience.

    Client-facing mail (document sent/shared, request raised) uses the ``client`` branding; staff
    mail the ``platform`` one. Falls back to Graphene's own brand for an unknown/absent tenant, so
    a mail path never fails because branding is unset."""
    if tenant_id is None:
        return GRAPHENE_BRAND
    key = (int(tenant_id), portal)
    hit = _brand_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    row = await _tenant_row(session, int(tenant_id))
    if row is None:
        return GRAPHENE_BRAND
    name, theme = row
    try:
        b = branding_mod.parse(theme, portal)
    except ValueError:  # a legacy/invalid theme must not break a send
        b = branding_mod.Branding()
    primary = b.colors.light.primary or ""
    masthead, icon = await _email_asset_urls(session, int(tenant_id), portal, b)
    brand = Brand(
        name=name or GRAPHENE_BRAND.name,
        logo_url=masthead,
        accent=primary if primary.startswith("#") else GRAPHENE_BRAND.accent,
        support_email=b.support.email or None,
        icon_url=icon,
    )
    _brand_cache[key] = (now, brand)
    return brand


async def _active_from_address(session: AsyncSession | None, tenant_id: int) -> str | None:
    """The mailbox a verified tenant domain sends as, or None to keep ``EMAIL_FROM``.

    A session of None is the unit-test stand-in for "no tenant domain": those tests resolve a
    brand without a database, and a missing row is the same answer a real firm without a verified
    domain gets. An unverified row, or a verified one with no address chosen yet, is also None —
    mail must not leave from a domain Resend has not accepted.
    """
    if session is None:
        return None
    row = (
        await session.execute(
            text(
                "SELECT from_address FROM email.sender_domains "
                "WHERE tenant_id = :t AND sending_verified AND from_address IS NOT NULL"
            ),
            {"t": int(tenant_id)},
        )
    ).scalar_one_or_none()
    if not isinstance(row, str) or not row.strip():
        return None
    return row.strip()


def _firm_envelope(*, brand: Brand, verified: str, support: str, tenant_domain: bool) -> Sender:
    """The firm's own envelope.

    On the platform sender, a support address that already sits on that verified domain becomes
    the From — that is the behaviour every firm has today. On a tenant's own verified domain the
    From is the address operations chose, and the support address stays the Reply-To even when it
    happens to share the domain. A reply and a sending mailbox are different jobs.
    """
    if tenant_domain:
        reply = support if support and support.lower() != verified.lower() else None
        return Sender(_mailbox(brand.name, verified), reply_to=reply)
    if support and _domain(support) == _domain(verified):
        return Sender(_mailbox(brand.name, support))
    return Sender(_mailbox(brand.name, verified), reply_to=support or None)


async def resolve_sender(
    session: AsyncSession,
    tenant_id: int | None,
    *,
    portal: branding_mod.Portal = "client",
    on_behalf_of: OnBehalfOf | None = None,
) -> Sender:
    """The envelope identity a tenant's mail goes out as.

    A firm with a verified sending domain (``email.sender_domains``, and an address chosen on it)
    sends from that address, wearing the firm's display name, with the firm's support address as
    ``Reply-To``. Every other firm keeps the platform sender: the support address becomes the
    ``From`` only when it sits on the SAME domain as ``EMAIL_FROM`` — Resend refuses an unverified
    domain, so an arbitrary firm address would silently kill delivery. Otherwise we keep the
    verified address, wear the firm's display name, and put theirs in ``Reply-To``.

    ``on_behalf_of`` sends AS A PERSON — an adviser mailing their own client — under exactly the
    same deliverability rule, because the rule is Resend's and not ours. Where the adviser's
    address sits on the verified domain (the tenant's, when they have one, otherwise the
    platform's) it becomes the literal ``From``. Where it does not, the verified address is kept,
    the display name names the adviser AND the firm ("Sarah Whitfield (Acme Wealth)"), and the
    adviser's own address becomes the ``Reply-To``.

    THE ADVISER PATH IS NEVER CACHED. ``_sender_cache`` is keyed by tenant and portal only, so
    storing a person's envelope under it would hand the next caller for that firm an adviser's
    ``From`` — the same class of failure as ``sender_for``'s silent miss, in the other direction.
    The brand read below is cached anyway, so the cost of resolving fresh is nothing.
    """
    default = Sender(settings.email_from)
    if tenant_id is None:
        return default
    # A PERSON's envelope, resolved fresh and never stored — see the note above.
    if on_behalf_of is not None:
        configured = await _active_from_address(session, int(tenant_id))
        verified = configured or _address(settings.email_from)
        brand = await brand_for(session, tenant_id, portal=portal)
        adviser = (on_behalf_of.email or "").strip()
        if adviser and _domain(adviser) == _domain(verified):
            return Sender(_mailbox(on_behalf_of.name, adviser))
        # Both names, because the firm is who the client has a relationship with and the adviser is
        # who they are hearing from; either alone leaves the header answering half the question.
        who = on_behalf_of.name.strip()
        display = f"{who} ({brand.name})" if who and brand.name else who or brand.name
        # The adviser first: the whole point is that a reply reaches the person. The firm's support
        # address is the fallback, so a reply still goes somewhere a human reads.
        return Sender(
            _mailbox(display, verified),
            reply_to=adviser or (brand.support_email or "").strip() or None,
        )
    key = (int(tenant_id), portal)
    hit = _sender_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    configured = await _active_from_address(session, int(tenant_id))
    verified = configured or _address(settings.email_from)
    brand = await brand_for(session, tenant_id, portal=portal)
    sender = _firm_envelope(
        brand=brand,
        verified=verified,
        support=(brand.support_email or "").strip(),
        tenant_domain=configured is not None,
    )
    _sender_cache[key] = (now, sender)
    return sender


def sender_for(tenant_id: int | None = None) -> str:
    """The ``From`` address for a tenant, WITHOUT a DB round trip — the already-resolved value when
    ``resolve_sender`` has warmed the cache for this tenant, else the configured Graphene sender.

    **DO NOT REACH FOR THIS FROM A SITE THAT HOLDS A SESSION.** The cache is process-local, 5-minute
    TTL and filled only by ``resolve_sender``, so a miss is both easy and SILENT: the firm-branded
    mail goes out from the Graphene address with the firm's ``Reply-To`` dropped, which reads as
    phishing. The worker processes (the outbox dispatcher, the task notifier) can never warm it at
    all — they import their own ``app.email`` and so hold their own permanently empty dict. Every
    send site with a session resolves instead; the outbox, which has none, carries the identity its
    producer resolved in the payload (``tests/test_email_sender_identity.py``).

    What is left for this: the no-tenant default inside ``send_email``, and a legacy outbox row
    written before the payload carried the keys."""
    if tenant_id is None:
        return settings.email_from
    for portal in ("client", "platform"):
        hit = _sender_cache.get((int(tenant_id), portal))
        if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1].from_addr
    return settings.email_from


# ---- the non-production send guard (ON by default, dev and staging only) -----------------------
# `EMAIL_NONPROD_GUARD` is the master switch and it ships ON. With it on, `APP_STAGE` of `dev` or
# `staging` mails only what matches the allowlist. `EMAIL_ALLOWLIST` is that list's DEFAULT —
# `@grapheneplatforms.com` when the variable is unset — and a row in
# `auth.outbound_email_allowlist` (the admin tenant page) REPLACES it for the whole environment
# until reset. An EMPTY list, a saved one or an explicit blank env value, suppresses EVERY send
# on those two stages: a cleared value means "mail nobody", because a staging box inherits a
# working `RESEND_API_KEY` far sooner than a curated list. Every other stage, including
# production, mails freely. With the switch off, `suppression_reason` returns None in every
# stage. THE VALUE CHECKED IS THE VALUE SENT.

#: The only stages the allowlist applies to. Matched exactly after strip and lower, so `Dev` is
#: guarded and `production` is not. Any other name — including a blank or a typo — mails freely.
GUARDED_STAGES = frozenset({"dev", "staging"})


def stage_is_guarded() -> bool:
    return settings.app_stage.strip().lower() in GUARDED_STAGES


def configured() -> bool:
    """True when a Resend credential is present. The credential itself is read nowhere else in the
    app (see this module's docstring) — health/metrics ask through here."""
    return bool(settings.resend_api_key)


# ``_UNSET`` means "no saved list loaded": use ``EMAIL_ALLOWLIST``. A list, including an empty
# one, is a saved replacement. Tests and the admin save set it directly; ``refresh_allowlist``
# loads it from ``auth.outbound_email_allowlist``.
_UNSET: object = object()
_runtime_allowlist: list[str] | object = _UNSET


def clear_runtime_allowlist() -> None:
    """Forget a saved list so the next check uses ``EMAIL_ALLOWLIST`` again."""
    global _runtime_allowlist
    _runtime_allowlist = _UNSET


def set_runtime_allowlist(entries: list[str]) -> None:
    """Install a saved list for this process. An empty list mails nobody."""
    global _runtime_allowlist
    _runtime_allowlist = [entry.strip().lower() for entry in entries if entry.strip()]


def default_allowlist() -> list[str]:
    """``EMAIL_ALLOWLIST``, normalised.

    What a dev or staging box uses until someone saves a list.
    """
    return [entry.strip().lower() for entry in settings.email_allowlist if entry.strip()]


def _allowlist() -> list[str]:
    """The list a send is checked against.

    The saved one, or ``EMAIL_ALLOWLIST`` when none is saved.
    """
    if isinstance(_runtime_allowlist, list):
        return list(_runtime_allowlist)
    return default_allowlist()


class AllowlistEntryError(ValueError):
    """One saved entry is not an address or a domain. ``entry`` is what was rejected."""

    def __init__(self, entry: str) -> None:
        self.entry = entry
        super().__init__(entry)


class AllowlistTooLong(AllowlistEntryError):
    """More than 100 entries. A list that long is not a recipient list."""


def normalise_allowlist_entries(raw: Sequence[str]) -> list[str]:
    """The saved form of an allowlist: trimmed, lowercased, domains written ``@host``, duplicates
    dropped. A blank item is skipped. Anything that is not one address or one domain raises
    ``AllowlistEntryError`` — a multi-address line must not become a way to widen the list by
    accident. An empty result is legal and means mail nobody."""
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text_value = item.strip().lower()
        if not text_value:
            continue
        if len(text_value) > 254:
            raise AllowlistEntryError(text_value[:254])
        normalised = _normalise_allowlist_entry(text_value)
        if normalised not in seen:
            seen.add(normalised)
            out.append(normalised)
    if len(out) > 100:
        raise AllowlistTooLong(f"{len(out)} entries")
    return out


def _normalise_allowlist_entry(entry: str) -> str:
    if any(char in entry for char in (*_MULTI_ADDRESS_CHARS, " ", "\t")):
        raise AllowlistEntryError(entry)
    host = entry[1:] if entry.startswith("@") else entry
    if entry.startswith("@"):
        if not _allowlist_host(host):
            raise AllowlistEntryError(entry)
        return "@" + host
    if "@" in entry:
        local, _, domain = entry.partition("@")
        if not local or not _allowlist_host(domain):
            raise AllowlistEntryError(entry)
        return entry
    if not _allowlist_host(entry):
        raise AllowlistEntryError(entry)
    return "@" + entry


def _allowlist_host(host: str) -> bool:
    if not host or "@" in host or host.startswith((".", "-")) or host.endswith((".", "-")):
        return False
    if ".." in host:
        return False
    return all(char.isalnum() or char in ".-" for char in host)


async def read_stored_allowlist(session: AsyncSession) -> list[str] | None:
    """The saved list, or None when this environment is still on the default."""
    row = (
        await session.execute(
            text("SELECT entries FROM auth.outbound_email_allowlist WHERE singleton")
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return [str(entry) for entry in row]


async def write_stored_allowlist(session: AsyncSession, entries: list[str]) -> None:
    await session.execute(
        text(
            "INSERT INTO auth.outbound_email_allowlist (singleton, entries) "
            "VALUES (true, CAST(:entries AS text[])) "
            "ON CONFLICT (singleton) DO UPDATE "
            "SET entries = EXCLUDED.entries, updated_at = now()"
        ),
        {"entries": entries},
    )


async def clear_stored_allowlist(session: AsyncSession) -> None:
    await session.execute(text("DELETE FROM auth.outbound_email_allowlist"))


async def refresh_allowlist() -> None:
    """Load the saved list into this process. No row clears it back to ``EMAIL_ALLOWLIST``.

    Called from the send chokepoint and from the places that report why mail did not leave, so
    they agree. A read failure keeps the list already in use: a database blip must not widen
    who a staging box will mail. Outside dev and staging there is nothing to load."""
    if not settings.email_nonprod_guard or not stage_is_guarded():
        clear_runtime_allowlist()
        return
    try:
        from app.db import system_transaction

        async with system_transaction() as session:
            stored = await read_stored_allowlist(session)
    except Exception as exc:
        logger.warning(
            "outbound email allowlist could not be read (%s); keeping the list already in use",
            type(exc).__name__,
        )
        return
    if stored is None:
        clear_runtime_allowlist()
    else:
        set_runtime_allowlist(stored)


def recipient_allowed(to: str, allowlist: list[str]) -> bool:
    """Does this recipient match the allowlist? Pure, so the matching rules are directly testable.

    An entry is either a full address (``craig@grapheneplatforms.com``) or a domain — written
    ``@grapheneplatforms.com`` or bare. Domains match the recipient's domain EXACTLY: no subdomain
    or suffix matching, because ``@example.com`` quietly admitting ``@clients.example.com`` is the
    kind of generosity that ends with a real client being mailed from staging.

    A recipient that isn't address-shaped — no local part, no domain, or MORE THAN ONE mailbox —
    matches nothing: there is nothing to compare, and "cannot be checked" must resolve to "not
    allowed". The multi-mailbox arm is ``_single_address``, and it is the security-relevant one: see
    that function for what a permissive reading admitted."""
    single = _single_address(to)
    if single is None:
        return False
    addr = single.lower()
    local, _, domain = addr.partition("@")
    if not local or not domain:  # pragma: no cover — `_single_address` already guarantees both
        return False
    for raw in allowlist:
        # Normalised here too: this function is the rule, so it must not rely on a tidy list.
        entry = raw.strip().lower().removeprefix("@")
        if not entry:
            continue
        if "@" in entry:  # a full address
            if addr == entry:
                return True
        elif domain == entry:  # a domain, written @host or bare
            return True
    return False


def stage_guard_suppresses_all() -> bool:
    """True when the stage guard alone suppresses every recipient (``dev`` or ``staging`` with an
    empty allowlist), independently of whether Resend is configured. Exposed for the metric: a
    staging box holding a real key and mailing nobody is the state we WANT, and it must be
    distinguishable from a box with no key at all.

    Gated on the master switch (`EMAIL_NONPROD_GUARD`, on by default) and on the stage being
    exactly ``dev`` or ``staging``. With the guard disabled, or on any other stage, this is
    False regardless of the allowlist."""
    return settings.email_nonprod_guard and stage_is_guarded() and not _allowlist()


def suppression_reason(to: str) -> str | None:
    """Why this recipient must NOT be mailed on this deployment, or None to proceed.

    The reason names the setting, never the recipient: it is written into a log line, and a log
    line is not a place to put a client's email address.

    The guard is on by default (`EMAIL_NONPROD_GUARD`) and applies only when `APP_STAGE` is
    ``dev`` or ``staging``. When disabled, or on any other stage, it never suppresses, so a send
    is best-effort and gated only by whether Resend is configured. On those two stages a recipient
    is mailed only if they match `EMAIL_ALLOWLIST`, which defaults to `@grapheneplatforms.com`."""
    if not settings.email_nonprod_guard or not stage_is_guarded():
        return None
    allowlist = _allowlist()
    if not allowlist:
        return f"APP_STAGE={settings.app_stage!r} is dev or staging and EMAIL_ALLOWLIST is empty"
    if recipient_allowed(to, allowlist):
        return None
    return (
        f"APP_STAGE={settings.app_stage!r} is dev or staging and the recipient is not on "
        "EMAIL_ALLOWLIST"
    )


def sending_disabled() -> str | None:
    """Why EVERY send on this deployment is a no-op, or None when some mail can leave.

    Two independent reasons, both legitimate states rather than faults: no Resend credential, or
    ``dev``/``staging`` with an empty allowlist. Read by the worker's notifier loop (so a dev box
    doesn't scan the task tables every minute for sends that cannot happen, re-reading the same
    unstamped rows for ever) and by ``/health/ready`` (so "email: ok" doesn't mean "silently
    suppressed"). A NON-empty allowlist is not "disabled" — those recipients do get mail."""
    if not configured():
        return "RESEND_API_KEY is not set"
    if stage_guard_suppresses_all():
        return f"APP_STAGE={settings.app_stage!r} is dev or staging and EMAIL_ALLOWLIST is empty"
    return None


def _wire_payload(
    *,
    address: str,
    subject: str,
    html: str,
    sender: str | None,
    reply_to: str | None,
    attachments: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    """The JSON body posted to Resend. Shared with the ledger so Raw is the request, not a second
    copy of the preview HTML."""
    payload: dict[str, object] = {
        "from": sender or sender_for(),
        "to": [address],
        "subject": subject,
        "html": html,
    }
    if attachments:
        payload["attachments"] = attachments
    if reply_to:
        payload["reply_to"] = reply_to
    return payload


#: What stands in for the wire body when it is the same markup the ``html`` column already holds.
#: A MARKER, not a removal: a request whose body simply has no ``html`` key reads as "we sent an
#: empty email". The detail pane recognises it and shows the stored body in its place.
SAME_AS_PREVIEW = "[…the body as stored on this message — the wire copy was identical…]"


def _request_for_ledger(
    payload: dict[str, object], *, preview_html: str | None = None
) -> dict[str, object]:
    """The POST as recorded: the JSON the wire saw, minus attachment bytes and minus a body the
    ledger is already storing.

    Bytes are the inlined images and can be hundreds of KB apiece; filenames and content-ids are
    what a reader needs to see that they went. The Authorization header is never on this object —
    httpx sends it separately, and reconstructing it here would put the Resend credential in the
    ledger.

    **The body is stored ONCE.** ``comms.messages.html`` already holds it for the preview, and for
    an email with no inlined images the wire copy is byte-identical — measured, not assumed — so
    keeping both doubled the largest column on the table for nothing, with no pruning job behind
    it. When they match, the request records ``SAME_AS_PREVIEW`` and the pane splices the stored
    body back in.

    When they DIFFER the wire copy is kept in full, because that is the case where it says
    something the preview cannot: inlining rewrites the image URLs into ``cid:`` references, and
    "did the logo actually go as an attachment" is answerable only from the markup that referenced
    it.
    """
    body = dict(payload)
    attachments = body.get("attachments")
    if isinstance(attachments, list):
        body["attachments"] = [
            {k: v for k, v in item.items() if k != "content"} if isinstance(item, dict) else item
            for item in attachments
        ]
    if preview_html is not None and body.get("html") == preview_html:
        body["html"] = SAME_AS_PREVIEW
    return {"method": "POST", "url": RESEND_ENDPOINT, "body": body}


def _provider_message_id(resp: httpx.Response) -> str | None:
    """Resend's id for the message it just accepted, or None.

    **Cannot raise.** It is called inside the send's own ``try``, so an exception here would be
    caught by the ``except`` below and a mail Resend had already ACCEPTED would be recorded as
    failed — the ledger asserting the opposite of what happened. A missing id costs the webhook its
    ability to match later callbacks; a wrong status costs a firm the truth.
    """
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - see the docstring: never raise on the success path
        return None
    if not isinstance(body, dict):
        return None
    value = body.get("id")
    return value[:200] if isinstance(value, str) and value else None


@dataclass(frozen=True)
class RetrievedEmailEvent:
    """Resend's current opinion of one message.

    NO TIMESTAMP, deliberately. The retrieval shape carries ``created_at``, which is when the EMAIL
    record was created — not when ``last_event`` happened. Stamping a delivery with it puts
    ``delivered_at`` BEFORE ``sent_at`` (which `ledger.record` sets after the POST returns), and
    the timeline sorts by instant, so every polled message rendered "Delivered 11:10:55 → Sent
    11:10:56". The honest answer is to supply none and let `apply_provider_event`'s ``now()`` arm
    record when we LEARNED, which is what this module's own header claims happens.
    """

    event: str


def _last_event_of(body: object) -> RetrievedEmailEvent | None:
    """``last_event`` and its provider timestamp out of a ``GET /emails/{id}`` body.

    Its own function so the FIELD NAME is pinned by a test without a network call. Verified against
    a real response (03 Sep 2026): Resend answers ``{"object": "email", "id": …, "last_event":
    "delivered", "created_at": …}``. If that field is renamed every message silently reads as "no
    news" — a permanent no-op no other test could tell from a quiet day, which is why it is pinned.
    """
    if not isinstance(body, dict):
        return None
    value = body.get("last_event")
    if not isinstance(value, str) or not value:
        return None
    # `created_at` is read and DISCARDED on purpose — see RetrievedEmailEvent.
    return RetrievedEmailEvent(event=value)


#: How many messages one list call returns. Resend's maximum, and the whole point of using the list
#: endpoint at all: a pass that used to spend one HTTP request PER pending message now spends one
#: for up to a hundred of them.
LIST_PAGE_SIZE = 100


async def fetch_recent_events(
    *, limit: int = LIST_PAGE_SIZE
) -> tuple[dict[str, RetrievedEmailEvent], bool] | None:
    """Resend's current opinion of its most recent messages, in ONE request.

    ``({provider_message_id: event}, has_more)``, or ``None`` when the credential is absent or the
    call fails — the same "no news" contract as :func:`fetch_last_events`, so a caller cannot tell a
    provider outage from a quiet period and does not act on the difference.

    WHY THIS EXISTS. The poll asked ``GET /emails/{id}`` once per pending message, so a pass cost as
    many round trips as it had rows to check. ``GET /emails`` answers the same question for a whole
    page at once, and the answer is the same shape — ``last_event`` per id — because it is the same
    field. Verified against a real account (07 Sep 2026): the list item carries ``id``,
    ``last_event`` and ``created_at``, and the envelope carries ``has_more``.

    NEWEST FIRST AND UNFILTERED, which is the catch and the reason the per-id reader stays: there is
    no way to ask for "these ids" or "everything since", so a message older than the page window
    simply is not in the answer. Absence here therefore means "not on this page", NEVER "no news" —
    a caller must not treat a missing id as an unresolved message, and `poll` falls back to the
    per-id call for exactly that tail.

    There is still no per-event timestamp. `GET /emails/{id}/events` does not exist (405, checked),
    so the only place a real event time is published is the webhook payload; see
    :class:`RetrievedEmailEvent`.
    """
    if not settings.resend_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{RESEND_ENDPOINT}?limit={int(limit)}",
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            )
            resp.raise_for_status()
            body = resp.json()
    except Exception as exc:  # noqa: BLE001 - a fallback must not raise into the worker loop
        logger.warning(
            "comms: could not list recent messages from the provider (%s)", type(exc).__name__
        )
        return None

    if not isinstance(body, dict):
        return None
    items = body.get("data")
    if not isinstance(items, list):
        return None

    out: dict[str, RetrievedEmailEvent] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        message_id = item.get("id")
        if not isinstance(message_id, str) or not message_id:
            continue
        # THE SAME EXTRACTOR the per-id reader uses, not a second copy of the same three lines.
        # The docstring's claim that this is "the same field" is worth nothing if the two paths read
        # it separately: `_last_event_of` is where the field name is PINNED by a test, and a rename
        # that this loop had to be told about separately is exactly the silent permanent no-op that
        # test exists to prevent.
        if (answer := _last_event_of(item)) is not None:
            out[message_id] = answer
    return out, bool(body.get("has_more"))


async def fetch_last_events(
    provider_message_ids: Sequence[str],
) -> dict[str, RetrievedEmailEvent]:
    """Resend's CURRENT opinion of each message (``GET /emails/{id}`` → ``last_event``).

    Lives here because this module is the only one allowed to hold the Resend credential — the rule
    ``tests/test_email_send_guard.py`` enforces, and it caught the delivery poll reaching for the
    key itself.

    A BATCH, sharing one client: Resend has no bulk read, but one ``AsyncClient`` across the batch
    is one TLS handshake instead of one per message — the difference between a pass costing a
    connection and a pass costing twenty-five.

    Never raises, and a message the provider has nothing to say about is simply absent from the
    answer. A 404 is an ordinary outcome, not a fault: a message pruned at the provider, and one
    sent by another deployment sharing the account, look exactly alike.
    """
    if not configured() or not provider_message_ids:
        return {}
    out: dict[str, RetrievedEmailEvent] = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for message_id in provider_message_ids:
            # `quote` leaves dots alone, so an id of `..` would still be a traversal segment even
            # though `/` cannot survive. Nothing Resend issues looks like this, which is the point:
            # an id that is only dots is not an id.
            if not message_id.strip(". "):
                continue
            try:
                resp = await client.get(
                    # QUOTED: the id is provider-controlled text off `comms.messages`, and an
                    # unescaped `/` or `?` in it would change which endpoint this reaches under our
                    # own bearer token. `safe=""` so a slash cannot survive as a path separator.
                    f"{RESEND_ENDPOINT}/{quote(message_id, safe='')}",
                    headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                )
                if resp.status_code == 404:
                    continue
                resp.raise_for_status()
                last_event = _last_event_of(resp.json())
            except Exception:  # noqa: BLE001 - see the docstring
                logger.debug("could not read a message back from Resend", exc_info=True)
                continue
            if last_event is not None:
                out[message_id] = last_event
    return out


async def send_email(
    *,
    to: str,
    subject: str,
    html: str,
    sender: str | None = None,
    reply_to: str | None = None,
    ctx: ledger.SendContext | None = None,
) -> bool:
    """Send one transactional email via Resend. THE chokepoint: every send site in the API funnels
    through here, which is what lets the non-production guard be one check rather than a convention.

    Returns False (and logs) when Resend isn't configured, when ``to`` does not name exactly one
    mailbox, when the stage guard SUPPRESSES the recipient, or when the send fails; never raises, so
    callers (provisioning, document sends, the task notifier, a flow action) don't hard-fail on an
    email outage or on being run in dev. False therefore means "not delivered" — deliberately not
    three-valued, because that is already the contract every caller was written against and a
    suppressed send is, to a caller, exactly as undelivered as an unconfigured one. Ask
    ``suppression_reason``/``sending_disabled`` when the difference matters (health, the worker's
    loops, the flow email action, the authoring surface's test send).

    **The recipient is validated and then POSTED, in that order, as the same value** — the
    normalised single address ``_single_address`` returns. The payload used to carry the raw
    original string while the guard checked a normalised derivation of it, so they differ; see
    ``_single_address``. That check is stage-independent, unlike the allowlist: production reads no
    allowlist, but a ``to`` naming two people is malformed data everywhere.

    Nothing about the recipient reaches the log but its DOMAIN: enough to tell "the allowlist is
    wrong" from "the stage is wrong", and no more.

    **Every outcome is recorded** to ``comms.messages`` (``app/comms/ledger.py``) — including the
    ones where nothing was posted, because "we deliberately did not send this, and here is which
    setting decided that" is the answer a firm needs far more often than a failure is. The one
    exception is the malformed-recipient branch, which records nothing: it refuses precisely
    because we could not decide what the string means, and a ledger row would have to store it.

    ``ctx`` carries WHY (the firm, the event, the rule, the template). It is optional so a call site
    that has not been threaded yet still produces a row — an unattributed one, visible to Graphene
    operations — rather than a hole in the ledger. The ledger writes on its own connection and never
    raises, so none of this can fail a send."""
    if not configured():
        logger.warning(
            "RESEND_API_KEY not set — no email sent (recipient domain %s, subject %r)",
            _domain(_address(to)) or "-",
            subject,
            extra={"event": "email_unconfigured", "recipient_domain": _domain(_address(to))},
        )
        # The VALIDATED address, or no row at all — the same rule the malformed branch below
        # states and for the same reason. This branch runs BEFORE `_single_address`, so it used
        # `_address(to)`, which is the historic mis-derivation its own docstring warns about
        # (`rpartition` on the last `@`, so `a@evil.test, b@ok.test` records the whole string with
        # a domain of `ok.test`). 0111 says the column holds the normalised single address and
        # never the caller's raw `to`; a value we cannot decide the meaning of is one we cannot
        # store, however early we noticed there was no credential.
        unconfigured_address = _single_address(to)
        if unconfigured_address is not None:
            await ledger.record(
                ctx,
                to=unconfigured_address,
                subject=subject,
                status="unconfigured",
                html=html,
                request=_request_for_ledger(
                    _wire_payload(
                        address=unconfigured_address,
                        subject=subject,
                        html=html,
                        sender=sender,
                        reply_to=reply_to,
                    ),
                    preview_html=html,
                ),
                error="RESEND_API_KEY is not set",
            )
        return False
    address = _single_address(to)
    if address is None:
        # Nothing is logged, not even the domain: we could not decide what this string means.
        logger.error(
            "EMAIL REFUSED — the recipient is not a single mailbox (%d '@' in it; subject %r). "
            "Nothing was sent. A `to` naming more than one address cannot be honestly checked "
            "against EMAIL_ALLOWLIST, because the value validated would not be the value posted.",
            to.count("@"),
            subject,
            extra={"event": "email_recipient_malformed", "at_count": to.count("@")},
        )
        # DELIBERATELY NOT RECORDED. Every other branch writes a ledger row, and this one must not:
        # `comms.messages.to_address` is NOT NULL and would have to hold this value, and the whole
        # reason we are here is that we could not decide what the value means — so we cannot say
        # which part of it is safe to store any more than we could say which part is safe to log.
        return False
    # From here on the normalised `address` IS the recipient; `to` is not consulted again.
    # The saved list, when this stage has one. Outside dev and staging this returns without a
    # query. It runs BEFORE the check so the list consulted is the one the admin page saved.
    await refresh_allowlist()
    reason = suppression_reason(address)
    if reason is not None:
        logger.warning(
            "EMAIL SUPPRESSED — %s (recipient domain %s, subject %r). No mail left the building; "
            "add the address or its domain to EMAIL_ALLOWLIST to receive it here.",
            reason,
            _domain(address) or "-",
            subject,
            extra={
                "event": "email_suppressed",
                "stage": settings.app_stage,
                "recipient_domain": _domain(address),
            },
        )
        await ledger.record(
            ctx,
            to=address,
            subject=subject,
            status="suppressed",
            html=html,
            request=_request_for_ledger(
                _wire_payload(
                    address=address,
                    subject=subject,
                    html=html,
                    sender=sender,
                    reply_to=reply_to,
                ),
                preview_html=html,
            ),
            # The reason names the SETTING, never the recipient — and it is the thing that stops a
            # dev box's wall of 'suppressed' reading as "every email is broken".
            error=reason,
        )
        return False
    # OUR OWN IMAGES TRAVEL WITH THE MESSAGE. An author's uploaded picture is only visible to a
    # recipient if their client can reach our bucket, which needs it on a real https origin — so
    # until then every uploaded logo arrived broken. Document templates already answer this by
    # resolving their resources server-side (`render/renderers.py::_safe_url_fetcher`); the mail
    # equivalent is a content-id attachment. Only OUR configured storage origins are ever
    # fetched, and a failure leaves the URL alone — see `email_images`.
    #
    # THE FIRM'S OWN ASSETS travel as attachments, resolved from its bucket rather than fetched
    # from its portal — see `_own_assets`. `ctx` is optional, so an un-threaded call site has no
    # tenant to resolve against and its images stay URLs rather than silently becoming somebody
    # else's: an absolute portal URL still loads for a recipient who allows remote images.
    own = await _own_assets(html, ctx.tenant_id if ctx else None)
    body_html, inline = await email_images.inline_own_images(html, own_assets=own)
    # `address`, NOT `to`: the value the guard just validated is the value on the wire.
    payload = _wire_payload(
        address=address,
        subject=subject,
        html=body_html,
        sender=sender,
        reply_to=reply_to,
        attachments=[a.payload() for a in inline] if inline else None,
    )
    request = _request_for_ledger(payload, preview_html=html)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                RESEND_ENDPOINT,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            provider_id = _provider_message_id(resp)
    except Exception as exc:
        # The domain, not the address: this is the most-logged email event there is.
        logger.exception(
            "Resend email failed (recipient domain %s, subject %r)",
            _domain(address) or "-",
            subject,
            extra={"event": "email_send_failed", "recipient_domain": _domain(address)},
        )
        await ledger.record(
            ctx,
            to=address,
            subject=subject,
            status="failed",
            html=html,
            request=request,
            # The exception's own summary — httpx puts the status and URL in it and not the request
            # headers, so this cannot carry the Resend credential.
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
        return False
    # Recorded AFTER the request, outside the try, so a ledger problem can never be mistaken for a
    # send problem. `html` and not `body_html`: the stored copy is for the Activity preview, and
    # `body_html`'s `cid:` references only resolve inside a real mail client. `request` is the
    # opposite: it is what went on the wire, so Raw can show the POST rather than a second copy of
    # the preview.
    await ledger.record(
        ctx,
        to=address,
        subject=subject,
        status="sent",
        html=html,
        request=request,
        provider_message_id=provider_id,
    )
    return True
