"""Object storage (S3-compatible): proxied branding assets and private documents — docs/storage.md.

Tenant logos + CMS images are browser-public (including the logged-out login page) but the object
store itself may be internal-only. They are stored tenant-bucket-first with a layered fallback:

1. the tenant's own bucket (``files/tenant/…``), once its registry row is active;
2. the legacy shared public bucket (``settings.s3_bucket``), unchanged behaviour;
3. inline ``data:`` URI when no object storage is configured at all (stored straight into
   the tenant's branding jsonb — fine for assets capped at ``MAX_ASSET_BYTES``).

Portal uploads use ``proxy=True`` and receive only ``/_assets/{opaque-name}``; each portal relays
that path to the internal API, so endpoint, bucket and object key never reach the browser.

Documents (uploaded + generated) are **private**: a separate bucket
(``settings.s3_documents_bucket``) with no public URL. Bytes reach a browser only via a short
presigned URL or a proxied stream, and uploads are magic-byte sniffed against the common document
and raster-image formats (<= 25 MiB) — see "Private documents" below. Locally this is RustFS.

Uploads are infrequent (saving branding), so we run the sync boto3 client in a thread.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol
from urllib.parse import quote, urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app import tenant_storage
from app.config import settings

logger = logging.getLogger(__name__)


def content_disposition(disposition: str, name: str) -> str:
    """Build a ``Content-Disposition`` header value that survives HTTP's latin-1 header encoding.

    A document name can hold characters outside latin-1 (em dashes, curly quotes, accented client
    names) — putting one straight into ``filename="…"`` makes the ASGI server (or S3, via a
    presigned ``ResponseContentDisposition``) raise on encode. RFC 6266: emit an ASCII-only
    ``filename`` fallback plus a UTF-8 ``filename*`` for modern browsers. ``disposition`` is
    ``inline`` or ``attachment``."""
    clean = name.replace("\n", " ").replace("\r", " ").strip() or "document"
    ascii_name = clean.encode("ascii", "ignore").decode().replace('"', "").replace("\\", "").strip()
    fallback = ascii_name or "document"
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(clean, safe='')}"


# content-type -> extension, doubling as the allowlist of acceptable image types.
_EXT = {
    "image/svg+xml": "svg",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
}
MAX_ASSET_BYTES = 512 * 1024  # 512 KiB — logos/favicons are small; keeps them inline-fast
PUBLIC_ASSET_PREFIX = "/_assets/"
#: Where a proxied public asset LIVES in a tenant's own bucket — the one prefix carrying an
#: anonymous-read policy (docs/storage.md §4). Named because three places have to agree on it:
#: the upload writes it, `get_public_asset` reads it back, and the email image library maps a
#: `/_assets/{name}` path to the object key it publishes.
TENANT_PUBLIC_PREFIX = "files/tenant/"
_PUBLIC_ASSET_NAME = re.compile(r"^[A-Za-z0-9-]+-[0-9a-f]{16}\.(?:svg|png|jpg|webp|ico)$")


def public_asset_src(name_or_url: str) -> str:
    """Return the same-origin proxy path for a stored public asset.

    New uploads already return this shape.  Absolute URLs from the old public-bucket design are
    translated on read so existing branding/content starts using the proxy without rewriting rows.
    Unknown external URLs and data URIs are left alone.
    """
    if name_or_url.startswith(PUBLIC_ASSET_PREFIX):
        return name_or_url
    path = urlparse(name_or_url).path
    name = path.rsplit("/", 1)[-1]
    if _PUBLIC_ASSET_NAME.fullmatch(name) and ("/files/tenant/" in path or "/tenants/" in path):
        return f"{PUBLIC_ASSET_PREFIX}{name}"
    return name_or_url


def public_asset_name(src: str) -> str | None:
    """The opaque file name carried by a public proxy path, never a bucket or object-store URL."""
    if not src.startswith(PUBLIC_ASSET_PREFIX):
        return None
    name = src.removeprefix(PUBLIC_ASSET_PREFIX)
    return name if _PUBLIC_ASSET_NAME.fullmatch(name) else None


def proxy_asset_urls(value: str) -> str:
    """Translate legacy object-store URLs inside stored HTML to same-origin proxy paths."""
    return re.sub(r"https://[^\s\"'<>]+", lambda m: public_asset_src(m.group(0)), value)


#: A proxy path where it can appear in stored HTML: inside a quoted attribute (`src="…"`,
#: `href='…'`) or a CSS `url(…)`. Anchored on the delimiter rather than matched bare, so a path
#: written in prose or already absolute (`https://host/_assets/x`) is left exactly as it is —
#: the second case matters, because this function must be safe to run twice.
_QUOTED_ASSET = re.compile(
    r"(?P<open>[\"'(])"
    + re.escape(PUBLIC_ASSET_PREFIX)
    + r"(?P<name>[A-Za-z0-9-]+-[0-9a-f]{16}\.(?:svg|png|jpg|webp|ico))(?=[\"')])"
)


def absolute_asset_urls(value: str, origin: str) -> str:
    """The inverse of :func:`proxy_asset_urls`: same-origin proxy paths made ABSOLUTE against
    ``origin``.

    For email, and only for email. Every portal renders `/_assets/{name}` happily — it is a path on
    the origin the page is already served from — and a mail client has no such origin: a relative
    src in an inbox resolves against nothing and arrives broken. So the send path states the host,
    which is the firm's own portal (`transport.asset_origin`), and the recipient's client fetches
    the picture through the same relay a browser does.

    Idempotent, and that is a requirement rather than a nicety: `to_email_html` is the one seam
    every send path passes through, but a caller may hand it HTML that has already been through a
    preview. An absolute URL contains no quoted `"/_assets/`, so a second pass matches nothing.

    A blank ``origin`` returns the value untouched — a firm with no portal host registered has
    nowhere to point at, and a broken relative src is still better than one pointing at a host that
    does not exist.
    """
    if not origin or PUBLIC_ASSET_PREFIX not in value:
        return value
    base = origin.rstrip("/")
    return _QUOTED_ASSET.sub(
        lambda m: f"{m.group('open')}{base}{PUBLIC_ASSET_PREFIX}{m.group('name')}", value
    )


class StorageUnavailable(RuntimeError):
    """Object storage isn't configured (no bucket) — callers should surface a clear 4xx/hint."""


SHOWN_WHEN_UNCONFIGURED = "Document storage is unavailable. Contact support."
"""Every raise of the unconfigured case carries this, and nine handlers across three routers
answer 503 with `str(exc)` — so the message lives once, here, beside the two cases that raise it.
It named the variable to set, correctly, right up until a store was configured and unreachable and
that same sentence sent an operator to check something that was already right."""


class ObjectMissing(LookupError):
    """The row names an object the store no longer has (``NoSuchKey`` / 404).

    Distinct from ``StorageUnavailable`` on purpose: the store answered, and the answer was "not
    here". A caller maps it to 404 — the document exists as a record but its bytes are gone — where
    letting botocore's ``ClientError`` propagate used to surface as a 500 the portal dressed as
    "Document unavailable", indistinguishable from an outage."""


class StorageUnreachable(StorageUnavailable):
    """Object storage IS configured but the endpoint could not be reached.

    A SUBCLASS so that every `except StorageUnavailable` already in the codebase keeps catching it
    and keeps answering 503 — the two are the same outcome for a caller (the bytes did not land).
    They are not the same thing for whoever has to fix it, which is why this exists: a handler that
    reports the unconfigured case verbatim tells an operator to set `S3_DOCUMENTS_BUCKET` when the
    bucket is set perfectly well and the store simply is not running. Handlers that surface
    `str(exc)` therefore say which of the two it is."""


class BucketMissing(RuntimeError):
    """The requested bucket doesn't exist in the store — the registry names a bucket that was never
    minted (or the store was reset). Carries the name so a caller can fall back or report it."""


def is_configured() -> bool:
    """Whether ANY upload path short of the data-URI fallback exists (the legacy shared
    bucket; tenant buckets are resolved per-request). Kept for the editors' hinting."""
    return bool(settings.s3_bucket and settings.s3_public_base_url)


def _path_style() -> bool:
    """Whether to use path-style addressing (`endpoint/bucket/key`). Explicit override wins; else
    path-style whenever a custom endpoint is set (MinIO / S3-compatibles); virtual-host for AWS."""
    force = settings.s3_force_path_style.strip().lower()
    if force in {"1", "true", "yes", "on"}:
        return True
    if force in {"0", "false", "no", "off"}:
        return False
    return bool(settings.s3_endpoint_url)


@lru_cache
def client_for(endpoint: str, region: str | None) -> Any:
    """The cached per-endpoint client, for modules that read OTHER buckets than the ones the
    helpers here write to — app/storage_usage.py scans a tenant's own bucket."""
    return _client_for(endpoint, region)


def _client_for(endpoint: str, region: str | None) -> Any:
    """boto3 client for one endpoint, with the platform root credential. The API's own
    writes always use the platform credential — per-tenant service creds are for the
    ETL/datalake plane, not the request path (docs/storage.md §3).

    Cached per (endpoint, region): building a boto3 client loads botocore's service models
    from disk (tens of ms) and the low-level client is thread-safe, so every upload/presign/
    list would otherwise pay that cost afresh. The platform credential + path-style are
    process-stable, so the cache key needs only the endpoint pair (matches
    ``object_store._s3_client``)."""
    import boto3
    from botocore.config import Config

    # Force SigV4: presigned document URLs must use it (AWS S3's modern regions reject the legacy
    # SigV2 presign; R2/MinIO/RustFS accept SigV4 too), and it's fine for the public put path.
    #
    # The timeouts are ours because botocore's defaults are sized for a background job, not a
    # request a person is waiting on: 60s to connect, retried on botocore's `legacy` curve, is
    # minutes of a held request when the endpoint is simply not there — long enough for the portal,
    # the proxy or the client's patience to give out first, and every one of those reads as "the
    # upload is broken" rather than "the store is unreachable". `connect_timeout` is the one that
    # matters (an absent store fails there, and 5s is generous for the same docker network or VPC);
    # `read_timeout` stays roomy because it governs waiting for the response to a PUT that may
    # legitimately be 25 MiB.
    config = Config(
        signature_version="s3v4",
        s3={"addressing_style": "path" if _path_style() else "virtual"},
        connect_timeout=5,
        read_timeout=60,
        retries={"max_attempts": 3, "mode": "standard"},
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        region_name=region or None,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
        config=config,
    )


def _put(
    bucket: str,
    endpoint: str,
    region: str | None,
    key: str,
    data: bytes,
    content_type: str,
    *,
    acl: str = "",
) -> None:
    # Assets are public: send a public-read ACL where the provider supports it (settings.s3_acl),
    # otherwise the bucket/prefix must grant anonymous GET via a bucket policy.
    extra = {"ACL": acl} if acl else {}
    _client_for(endpoint, region).put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
        CacheControl="public, max-age=86400",
        **extra,
    )


async def put_branding_asset(
    session: AsyncSession,
    tenant_id: int,
    kind: str,
    data: bytes,
    content_type: str,
    *,
    proxy: bool = False,
) -> str:
    """Store a branding/content asset and return its same-origin proxy path. `kind` is a
    short label like 'logo' | 'logo-dark' | 'favicon'. Raises ValueError on a bad type/size.

    The browser never receives the object-store endpoint, bucket or key. The tenant's own bucket
    is preferred even when it has no public base URL: ``/_assets/{opaque-name}`` is relayed by each
    portal to the API's internal asset endpoint. Legacy shared storage and the data-URI fallback
    remain supported."""
    ext = _EXT.get(content_type)
    if not ext:
        raise ValueError(f"unsupported asset type '{content_type}'")
    if not data or len(data) > MAX_ASSET_BYTES:
        raise ValueError(f"asset must be 1..{MAX_ASSET_BYTES} bytes")

    # Content-hashed key: immutable URL per asset (safe long cache), and de-dupes re-uploads.
    digest = hashlib.sha256(data).hexdigest()[:16]

    store = await tenant_storage.get_tenant_store(session, tenant_id)
    if store and (proxy or store.public_base_url):
        key = f"{TENANT_PUBLIC_PREFIX}{kind}-{digest}.{ext}"
        await asyncio.to_thread(
            _put, store.bucket, store.endpoint, store.region, key, data, content_type
        )
        if proxy:
            return f"{PUBLIC_ASSET_PREFIX}{key.rsplit('/', 1)[-1]}"
        public_base = store.public_base_url
        if public_base is None:  # narrowed by the branch condition above
            raise StorageUnavailable("Public asset hosting is not configured.")
        return f"{public_base.rstrip('/')}/{key}"

    if settings.s3_bucket and (proxy or settings.s3_public_base_url):
        key = f"tenants/{tenant_id}/{kind}-{digest}.{ext}"
        await asyncio.to_thread(
            _put,
            settings.s3_bucket,
            settings.s3_endpoint_url,
            settings.s3_region or None,
            key,
            data,
            content_type,
            acl=settings.s3_acl,
        )
        if proxy:
            return f"{PUBLIC_ASSET_PREFIX}{key.rsplit('/', 1)[-1]}"
        return f"{settings.s3_public_base_url.rstrip('/')}/{key}"

    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{b64}"


def is_branding_type(content_type: str) -> bool:
    """Whether ``put_branding_asset`` stores ``content_type`` as it is."""
    return content_type in _EXT


# ── Email copies ──────────────────────────────────────────────────────────────
# A logo or icon stored as SVG, WebP or ICO gets a PNG twin for email (`app/imaging.py` says why).
# The twin is found from the ORIGINAL's name alone — same kind, same digest, a `-email` kind suffix,
# always `.png` — so nothing has to record that it exists, and a new logo (new bytes, new digest)
# can never be paired with the old logo's copy.

_EMAIL_COPY_SUFFIX = "-email"


def email_copy_name(name: str) -> str | None:
    """``logo-628afb62f38871ae.svg`` → ``logo-email-628afb62f38871ae.png``; None for a name the
    relay would refuse. The result fits the same grammar, so the relay serves it like any asset."""
    if not _PUBLIC_ASSET_NAME.fullmatch(name):
        return None
    stem = name.rsplit(".", 1)[0]
    kind, digest = stem.rsplit("-", 1)
    return f"{kind}{_EMAIL_COPY_SUFFIX}-{digest}.png"


def is_email_copy(name: str) -> bool:
    """Whether ``name`` is an email copy rather than something a person uploaded."""
    if not _PUBLIC_ASSET_NAME.fullmatch(name):
        return False
    return name.rsplit(".", 1)[0].rsplit("-", 1)[0].endswith(_EMAIL_COPY_SUFFIX)


async def put_email_copy(
    session: AsyncSession, tenant_id: int, name: str, png: bytes
) -> str | None:
    """Store ``png`` as the email copy of the asset ``name``; return the copy's name, or None when
    there is no object storage to hold it (the ``data:`` rung — email refuses a ``data:`` image
    anyway).

    The same two rungs, in the same order, as ``put_branding_asset(proxy=True)`` — which is also
    the order ``get_public_asset`` reads them in, so the copy is found wherever it lands."""
    copy = email_copy_name(name)
    if copy is None:
        raise ValueError("invalid asset")
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > MAX_ASSET_BYTES:
        raise ValueError("an email copy must be a PNG within the asset cap")
    store = await tenant_storage.get_tenant_store(session, tenant_id)
    if store:
        key = f"{TENANT_PUBLIC_PREFIX}{copy}"
        await asyncio.to_thread(
            _put, store.bucket, store.endpoint, store.region, key, png, "image/png"
        )
        return copy
    if settings.s3_bucket:
        key = f"tenants/{tenant_id}/{copy}"
        await asyncio.to_thread(
            _put,
            settings.s3_bucket,
            settings.s3_endpoint_url,
            settings.s3_region or None,
            key,
            png,
            "image/png",
            acl=settings.s3_acl,
        )
        return copy
    return None


def _client() -> Any:
    """Platform client for the shared-endpoint buckets (branding legacy + documents)."""
    return _client_for(settings.s3_endpoint_url, settings.s3_region or None)


# ── Private documents ─────────────────────────────────────────────────────────
# Documents are PRIVATE: a separate bucket (`settings.s3_documents_bucket`) with no public
# base URL. Bytes only reach a browser via a short presigned URL or a proxied stream. We
# reuse the same endpoint/credentials/addressing as the public asset bucket above.

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOC = "application/msword"
XLS = "application/vnd.ms-excel"
PPT = "application/vnd.ms-powerpoint"
ODT = "application/vnd.oasis.opendocument.text"
ODS = "application/vnd.oasis.opendocument.spreadsheet"
ODP = "application/vnd.oasis.opendocument.presentation"
TXT = "text/plain"
CSV = "text/csv"
RTF = "application/rtf"
HEIC = "image/heic"
HEIF = "image/heif"
AVIF = "image/avif"

# Allowed document content-type -> file extension (also the upload allowlist).
_DOC_EXT = {
    "application/pdf": "pdf",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/tiff": "tiff",
    "image/bmp": "bmp",
    HEIC: "heic",
    HEIF: "heif",
    AVIF: "avif",
    DOCX: "docx",
    XLSX: "xlsx",
    PPTX: "pptx",
    DOC: "doc",
    XLS: "xls",
    PPT: "ppt",
    ODT: "odt",
    ODS: "ods",
    ODP: "odp",
    TXT: "txt",
    CSV: "csv",
    RTF: "rtf",
}
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024  # 25 MiB (per the spec)


class PayloadTooLarge(ValueError):
    """An upload exceeded ``MAX_DOCUMENT_BYTES`` — raised by ``read_capped`` as soon as the cap is
    passed, so the rest of the body is never buffered. Callers map it to 413."""


class Readable(Protocol):
    """The slice of Starlette's ``UploadFile`` this module needs (kept web-framework-agnostic)."""

    async def read(self, size: int = -1, /) -> bytes: ...


# 1 MiB per read — small enough to abort an over-cap body early, large enough to stay fast.
READ_CHUNK_BYTES = 1024 * 1024


async def read_capped(file: Readable, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    """Read an upload in chunks, raising ``PayloadTooLarge`` the moment it exceeds ``limit``.

    A declared-size pre-check only helps when the client sends a Content-Length: a chunked upload
    reports ``file.size = None``, so ``await file.read()`` would buffer the whole (multi-GB) body
    into memory before ``validate_upload`` ever saw it. Reading with a running cap bounds what an
    unauthenticated-by-size client can make the API allocate to ``limit + one chunk``."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise PayloadTooLarge(f"document must be 1..{limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def documents_configured() -> bool:
    """Whether the private-document bucket is set. Unlike branding there is NO data-URI
    fallback — documents need real object storage, so callers surface a clear error."""
    return bool(settings.s3_documents_bucket)


def documents_bucket() -> str | None:
    """The shared private-documents bucket — the admin object browser's target until per-tenant
    buckets are provisioned, at which point the tenant's own bucket (storage registry) takes over.
    None when object storage is unconfigured."""
    return settings.s3_documents_bucket or None


def sniff_document_type(data: bytes, declared: str | None = None) -> str:
    """Resolve a document's content-type from its magic bytes (never trust the client's
    Content-Type on its own). Containers are opened and classified from their internal parts;
    text/CSV is accepted only after a clean UTF-8 decode. Returns the canonical type."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in {b"avif", b"avis"}:
            return AVIF
        if brand in {b"heic", b"heix", b"hevc", b"hevx"}:
            return HEIC
        if brand in {b"mif1", b"msf1"}:
            return HEIF
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        kind = _zip_document_kind(data)
        if kind:
            return kind
        raise ValueError("zip content did not match a supported Office or OpenDocument file")
    if data[:8] == _OLE2_MAGIC:
        kind = _legacy_office_kind(data)
        if kind:
            return kind
        raise ValueError("compound-file content did not match a supported legacy Office document")
    if data.lstrip().startswith(b"{\\rtf"):
        return RTF
    lowered = data.lstrip()[:64].lower()
    if lowered.startswith((b"<!doctype html", b"<html", b"<script", b"<svg", b"<?xml")):
        raise ValueError("active markup is not accepted as a text document")
    if _looks_like_text(data):
        normalised = (declared or "").split(";", 1)[0].strip().lower()
        return CSV if normalised in {CSV, "application/csv", "text/x-csv"} else TXT
    raise ValueError("unrecognised or unsupported document content")


def validate_upload(data: bytes, declared: str | None = None) -> tuple[str, str]:
    """Validate uploaded bytes (size + magic-byte type). Returns (content_type, ext).
    Raises ValueError on empty, oversize, or disallowed content."""
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"document must be 1..{MAX_DOCUMENT_BYTES} bytes")
    content_type = sniff_document_type(data, declared)
    return content_type, _DOC_EXT[content_type]


def document_ext(content_type: str) -> str:
    """File extension for an allowed document content-type. Raises ValueError otherwise."""
    ext = _DOC_EXT.get(content_type)
    if not ext:
        raise ValueError(f"unsupported document type '{content_type}'")
    return ext


# File extension -> document content-type: the reverse of `_DOC_EXT`.
_EXT_DOC = {ext: content_type for content_type, ext in _DOC_EXT.items()} | {"jpeg": "image/jpeg"}


def content_type_of(key: str) -> str:
    """The document content-type an object key's extension names ('a/front.pdf' ->
    'application/pdf'); ``application/octet-stream`` when it names none."""
    return _EXT_DOC.get(ext_of(key), "application/octet-stream")


def upload_key(tenant_uid: str, doc_uid: str, ext: str) -> str:
    """Storage key for a staff/client-uploaded original.

    LEGACY shape, kept for the shared documents bucket: it leads with the tenant uid because
    one bucket held every tenant. Rows written before the move to per-tenant buckets carry
    these keys, and `documents.files.bucket` says which bucket to read them from — so this
    must not change. New writes use `tenant_upload_key`.
    """
    return f"uploads/{tenant_uid}/{doc_uid}/original.{ext}"


def rendered_key(tenant_uid: str, doc_uid: str, version: int, ext: str) -> str:
    """Storage key for a generated/rendered document version (ext in {'pdf','html'}).
    LEGACY shape — see `upload_key`. New writes use `tenant_rendered_key`."""
    return f"documents/{tenant_uid}/{doc_uid}/v{version}.{ext}"


# The tenant-bucket layout (docs/storage.md §3). The tenant uid is NOT in the key: the bucket
# is the tenant, so repeating it buys nothing and makes every key longer than the thing it
# identifies. Everything a firm owns sits under `files/`, which is what the `documents` and
# `readonly` service users are scoped to (0061) — a key outside it is unreachable by them,
# so the prefix is a permission boundary rather than a naming convention.
FILES_PREFIX = "files/"


def tenant_upload_key(doc_uid: str, ext: str, *, contact_uid: str | None = None) -> str:
    """Key for an uploaded original in the tenant's own bucket.

    A document belonging to a client is filed under that client, so everything held about
    them is one prefix (subject-access requests and erasure both ask "what do you hold about
    this person?"). A staff document with no client is filed under `files/documents/`.
    """
    if contact_uid:
        return f"{FILES_PREFIX}clients/{contact_uid}/{doc_uid}/original.{ext}"
    return f"{FILES_PREFIX}documents/{doc_uid}/original.{ext}"


def tenant_rendered_key(
    doc_uid: str, version: int, ext: str, *, contact_uid: str | None = None
) -> str:
    """Key for a generated/rendered version in the tenant's own bucket. Versions live beside
    the original so one prefix is the whole history of that document."""
    if contact_uid:
        return f"{FILES_PREFIX}clients/{contact_uid}/{doc_uid}/v{version}.{ext}"
    return f"{FILES_PREFIX}documents/{doc_uid}/v{version}.{ext}"


def _resolve_bucket(bucket: str | None) -> str:
    """The bucket to act on: the caller's (a tenant's own, from `documents.files.bucket` or the
    storage registry) or the shared legacy one.

    Every document row already records its bucket — `documents.files` has carried a NOT NULL
    `bucket` since 0005, with `UNIQUE (bucket, storage_key)`. The read path simply ignored it
    and assumed the shared bucket, which is why moving documents into per-tenant buckets needs
    no migration of the rows: pass the recorded bucket and old and new coexist.
    """
    resolved = bucket or settings.s3_documents_bucket
    if not resolved:
        raise StorageUnavailable(SHOWN_WHEN_UNCONFIGURED)
    return resolved


def unreachable(exc: BaseException) -> bool:
    """Whether `exc` means the store was never reached (DNS, connect/read timeout, dropped
    connection) as opposed to having answered something.

    botocore raises these as `ConnectionError`/`HTTPClientError` subclasses, which carry the
    endpoint in their message — so a caller must translate rather than surface them: the endpoint
    and bucket are infrastructure detail, and `/health/ready` already withholds them for the same
    reason (`routers/health.py::_probe_detail`). Imported lazily, like boto3 itself."""
    from botocore.exceptions import ConnectionError as BotoConnectionError
    from botocore.exceptions import HTTPClientError

    return isinstance(exc, BotoConnectionError | HTTPClientError)


def _document_transport_error(exc: BaseException) -> StorageUnreachable:
    """The operator-facing message for a store that is configured and not answering. It names the
    variable to CHECK rather than one to set: the bucket is set, which is exactly what makes the
    "not configured" message misleading here."""
    logger.warning(
        "object storage is unreachable",
        extra={"event": "object_storage_unreachable", "error_type": type(exc).__name__},
        exc_info=True,
    )
    return StorageUnreachable(
        "Document storage is temporarily unavailable — the object store did not respond. "
        "Please try again shortly."
    )


def _put_document(key: str, data: bytes, content_type: str, bucket: str | None = None) -> None:
    # No ACL: document buckets are private (no public policy), and omitting the ACL keeps this
    # working on providers that reject ACLs (R2). Never publicly cached.
    try:
        _client().put_object(
            Bucket=_resolve_bucket(bucket),
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="private, no-store",
        )
    except Exception as exc:
        if unreachable(exc):
            raise _document_transport_error(exc) from exc
        raise


async def put_document(
    key: str, data: bytes, content_type: str, *, bucket: str | None = None
) -> None:
    """Store a private document object at `key`, in `bucket` (default: the shared legacy
    bucket). Raises StorageUnavailable if neither is configured."""
    if not bucket and not documents_configured():
        raise StorageUnavailable(SHOWN_WHEN_UNCONFIGURED)
    await asyncio.to_thread(_put_document, key, data, content_type, bucket)


def _presigned_get(
    key: str, expires: int, filename: str | None, disposition: str, bucket: str | None = None
) -> str:
    params: dict[str, str] = {"Bucket": _resolve_bucket(bucket), "Key": key}
    # Have S3 return a Content-Disposition (RFC-6266 encoded) so a presigned download saves under
    # the document's real name, not the opaque storage key — and honour inline vs attachment.
    if filename:
        params["ResponseContentDisposition"] = content_disposition(disposition, filename)
    # str(): boto3's client is Any-typed, so coerce for the -> str signature (mypy no-any-return).
    try:
        return str(_client().generate_presigned_url("get_object", Params=params, ExpiresIn=expires))
    except Exception as exc:
        if unreachable(exc):
            raise _document_transport_error(exc) from exc
        raise


async def presigned_get_document(
    key: str,
    expires: int = 900,
    *,
    filename: str | None = None,
    disposition: str = "attachment",
    bucket: str | None = None,
) -> str:
    """A short-lived (default 15-min) presigned GET URL for a private document. When ``filename``
    is given, the URL carries a ``ResponseContentDisposition`` so the download is named correctly
    (``disposition`` = ``attachment`` to save, ``inline`` to view in the browser)."""
    if not bucket and not documents_configured():
        raise StorageUnavailable(SHOWN_WHEN_UNCONFIGURED)
    return await asyncio.to_thread(_presigned_get, key, expires, filename, disposition, bucket)


# ── Object browser (admin) ─────────────────────────────────────────────────────
# Prefixes the object browser must NEVER surface: the datalake + archives are off the
# object-browsing path entirely (docs/storage.md — `lake/` must never be reachable). Enforced here,
# in one place, so every caller (and the future per-tenant bucket) inherits the exclusion.
_HIDDEN_PREFIXES = ("lake/", "datalake/", "archive/")


def is_hidden(name: str) -> bool:
    return any(name == p or name.startswith(p) for p in _HIDDEN_PREFIXES)


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    last_modified: str  # ISO-8601


@dataclass(frozen=True)
class ObjectPage:
    objects: list[StoredObject]
    folders: list[str]  # sub-"folder" common prefixes under the requested prefix
    next_token: str | None  # opaque continuation token for the next page, None when exhausted


def _list_objects(
    bucket: str,
    prefix: str,
    token: str | None,
    limit: int,
    delimiter: str,
    endpoint: str = "",
    region: str | None = None,
) -> ObjectPage:
    kwargs: dict[str, object] = {
        "Bucket": bucket,
        "Prefix": prefix,
        "MaxKeys": limit,
        "Delimiter": delimiter,
    }
    if token:
        kwargs["ContinuationToken"] = token
    from botocore.exceptions import ClientError

    client = client_for(endpoint, region) if endpoint else _client()
    try:
        resp = client.list_objects_v2(**kwargs)
    except ClientError as exc:
        # Surface a missing bucket as a typed error so the caller can fall back to a bucket that
        # exists (the admin browser drops to the shared documents bucket where the objects are).
        # Only NoSuchBucket becomes BucketMissing; every other S3 error still surfaces.
        if exc.response.get("Error", {}).get("Code") == "NoSuchBucket":
            raise BucketMissing(bucket) from exc
        raise
    folders = [p["Prefix"] for p in resp.get("CommonPrefixes", []) if not is_hidden(p["Prefix"])]
    objects = [
        StoredObject(key=o["Key"], size=int(o["Size"]), last_modified=o["LastModified"].isoformat())
        # Drop the folder placeholder key (equal to the prefix itself) and anything hidden.
        for o in resp.get("Contents", [])
        if o["Key"] != prefix and not is_hidden(o["Key"])
    ]
    return ObjectPage(
        objects=objects, folders=folders, next_token=resp.get("NextContinuationToken")
    )


async def list_objects(
    bucket: str,
    prefix: str = "",
    *,
    token: str | None = None,
    limit: int = 100,
    delimiter: str = "/",
    endpoint: str = "",
    region: str | None = None,
) -> ObjectPage:
    """One page of objects + sub-"folders" (common prefixes) under ``prefix`` in ``bucket`` — the
    admin object browser. Datalake/archive prefixes are never surfaced. ``delimiter='/'`` groups
    keys into folders (pass ``''`` to list every key flat). Paginate with the returned
    ``next_token``. Runs the sync boto3 call off the event loop.

    ``endpoint``/``region`` name the STORE to read, for a bucket that is not on the platform's
    default one — a tenant bucket resolved from the registry (``tenant_storage.get_tenant_store``)
    may sit on a different host, where the default client would look for it on the wrong one and
    get an answer indistinguishable from an empty bucket. An explicit endpoint also skips the
    ``S3_DOCUMENTS_BUCKET`` guard: the caller has already resolved a real bucket, and the shared
    private-documents bucket has no bearing on whether that one can be listed."""
    if not endpoint and not documents_configured():
        raise StorageUnavailable(SHOWN_WHEN_UNCONFIGURED)
    return await asyncio.to_thread(
        _list_objects, bucket, prefix, token, limit, delimiter, endpoint, region
    )


def _delete_objects_except(
    bucket: str,
    preserve_prefixes: tuple[str, ...],
    endpoint: str = "",
    region: str | None = None,
) -> int:
    """Delete every current object except explicitly preserved prefixes."""
    client = client_for(endpoint, region) if endpoint else _client()
    token: str | None = None
    deleted = 0
    while True:
        kwargs: dict[str, object] = {"Bucket": bucket, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        keys = [
            {"Key": obj["Key"]}
            for obj in page.get("Contents", [])
            if not any(obj["Key"].startswith(prefix) for prefix in preserve_prefixes)
        ]
        if keys:
            response = client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": keys, "Quiet": True},
            )
            errors = response.get("Errors") or []
            if errors:
                first = errors[0]
                raise StorageUnavailable(
                    f"could not delete {first.get('Key', 'object')}: "
                    f"{first.get('Code', 'storage error')}"
                )
            deleted += len(keys)
        if not page.get("IsTruncated"):
            return deleted
        token = page.get("NextContinuationToken")


async def delete_objects_except(
    bucket: str,
    *,
    preserve_prefixes: tuple[str, ...],
    endpoint: str = "",
    region: str | None = None,
) -> int:
    """Async wrapper used by tenant reset; branding is normally the sole preserved prefix."""
    return await asyncio.to_thread(
        _delete_objects_except, bucket, preserve_prefixes, endpoint, region
    )


@dataclass(frozen=True)
class PrefixStats:
    """Recursive rollup of everything under a prefix — the folder totals the browser shows."""

    size: int
    count: int
    last_modified: str | None  # ISO-8601 of the most-recent object; None when the prefix is empty
    exts: tuple[str, ...]  # distinct file extensions present (lowercased, no dot) — the type filter
    truncated: bool  # True if the scan hit `cap`; size/count are then a lower bound


def ext_of(key: str) -> str:
    """Lowercased file extension of an object key ('a/b/Report.PDF' -> 'pdf'); '' when none."""
    base = key.rsplit("/", 1)[-1]
    dot = base.rfind(".")
    return base[dot + 1 :].lower() if 0 < dot < len(base) - 1 else ""


def _prefix_stats(bucket: str, prefix: str, cap: int) -> PrefixStats:
    size = count = 0
    latest: str | None = None
    exts: set[str] = set()
    truncated = False
    token: str | None = None
    while True:
        kwargs: dict[str, object] = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = _client().list_objects_v2(**kwargs)
        for o in resp.get("Contents", []):
            key = o["Key"]
            if key.endswith("/") or is_hidden(key):  # folder placeholder / datalake+archive
                continue
            size += int(o["Size"])
            count += 1
            iso = o["LastModified"].isoformat()
            if latest is None or iso > latest:  # ISO-8601 sorts chronologically
                latest = iso
            e = ext_of(key)
            if e:
                exts.add(e)
            if count >= cap:
                truncated = True
                break
        if truncated or not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return PrefixStats(size, count, latest, tuple(sorted(exts)), truncated)


async def prefix_stats(bucket: str, prefix: str, *, cap: int = 5000) -> PrefixStats:
    """Recursively aggregate every object under ``prefix`` (total size, count, latest-modified, and
    the set of file extensions) — the folder rollup + type-filter options for the admin browser.
    Scans at most ``cap`` objects (``truncated`` flags when hit) so a huge folder can't stall a page
    load. Datalake/archive keys are never counted (``is_hidden``). Runs the boto3 calls off-loop."""
    if not documents_configured():
        raise StorageUnavailable(SHOWN_WHEN_UNCONFIGURED)
    return await asyncio.to_thread(_prefix_stats, bucket, prefix, cap)


def _get_document(key: str, bucket: str | None = None) -> tuple[bytes, str]:
    obj = _client().get_object(Bucket=_resolve_bucket(bucket), Key=key)
    return obj["Body"].read(), obj.get("ContentType", "application/octet-stream")


_MISSING_OBJECT_CODES = {"NoSuchKey", "NoSuchBucket", "404"}


async def get_document(key: str, *, bucket: str | None = None) -> tuple[bytes, str]:
    """Fetch a private document's bytes + content-type (for the proxied /pdf|/content stream).
    Pass the `bucket` recorded on the file row; omit it for the shared legacy bucket.

    Raises ``ObjectMissing`` when the store has no such object, so a caller can answer 404 rather
    than 500 for a row whose bytes are gone."""
    if not bucket and not documents_configured():
        raise StorageUnavailable(SHOWN_WHEN_UNCONFIGURED)
    from botocore.exceptions import ClientError

    try:
        return await asyncio.to_thread(_get_document, key, bucket)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in _MISSING_OBJECT_CODES:
            raise ObjectMissing(key) from exc
        raise


# ── Chat attachments ──────────────────────────────────────────────────────────
# One row per attached file (app/messaging.py). PRIVATE, and deliberately in the TENANT'S OWN
# bucket rather than the shared `settings.s3_documents_bucket` above — a chat attachment is
# exactly the per-tenant-sovereignty case `app/tenant_storage.py` exists for, and it never
# touches the public `files/tenant/` prefix (that one carries an anonymous-read bucket policy
# for branding). Attachments share the document sniffer's container helpers but intentionally keep
# their existing document-only subset (no inline images); the same `MAX_DOCUMENT_BYTES` ceiling
# applies — one size limit, one constant.

# Allowed attachment content-type -> file extension (also the upload allowlist).
_ATTACHMENT_EXT = {
    "application/pdf": "pdf",
    DOCX: "docx",
    XLSX: "xlsx",
    PPTX: "pptx",
    DOC: "doc",
    XLS: "xls",
    PPT: "ppt",
    TXT: "txt",
}

# OOXML (.docx/.xlsx/.pptx) share one zip signature; the only thing that tells them apart is
# which top-level part the zip actually contains.
_OOXML_PART_PREFIX = {"word/": DOCX, "xl/": XLSX, "ppt/": PPTX}
_OPENDOCUMENT_MIMETYPES = {ODT: ODT, ODS: ODS, ODP: ODP}

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _zip_document_kind(data: bytes) -> str | None:
    """Classify ZIP-based Office formats by contents, never by a browser-supplied MIME type."""
    import zipfile
    from io import BytesIO

    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = zf.namelist()
            try:
                mimetype = zf.read("mimetype").decode("ascii").strip()
            except (KeyError, UnicodeDecodeError):
                mimetype = ""
    except zipfile.BadZipFile:
        return None
    for prefix, content_type in _OOXML_PART_PREFIX.items():
        if any(n.startswith(prefix) for n in names):
            return content_type
    return _OPENDOCUMENT_MIMETYPES.get(mimetype)


def _legacy_office_kind(data: bytes) -> str | None:
    """Legacy .doc/.xls/.ppt are all one OLE2 compound-file format; the format itself
    mandates a specific top-level stream per application (MS-DOC/-XLS/-PPT), which is what
    actually distinguishes them — the container's own magic bytes do not."""
    from io import BytesIO

    import olefile

    # Keyword, not positional: olefile's bytes-as-first-positional-arg path is its own
    # deprecated legacy inference; `data=` is the explicit, documented in-memory-bytes API.
    if not olefile.isOleFile(data=data):
        return None
    try:
        with olefile.OleFileIO(BytesIO(data)) as ole:
            streams = {"/".join(path) for path in ole.listdir()}
    except OSError:
        return None
    if "WordDocument" in streams:
        return DOC
    if "Workbook" in streams or "Book" in streams:
        return XLS
    if "PowerPoint Document" in streams:
        return PPT
    return None


def _looks_like_text(data: bytes) -> bool:
    """No magic bytes exist for plain text — accepted only when it's clean UTF-8 with no
    embedded NUL (a renamed binary blob fails this)."""
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def sniff_attachment_type(data: bytes) -> str:
    """Resolve a chat attachment's content-type from its actual bytes — never the client's
    declared Content-Type, same rule as `sniff_document_type`. Raises ValueError for anything
    outside the allowlist."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        kind = _zip_document_kind(data)
        if kind in _ATTACHMENT_EXT:
            return kind
        raise ValueError("zip content did not match a supported Office document")
    if data[:8] == _OLE2_MAGIC:
        kind = _legacy_office_kind(data)
        if kind:
            return kind
        raise ValueError("compound-file content did not match a supported legacy Office document")
    if _looks_like_text(data):
        return TXT
    raise ValueError("unrecognised or unsupported attachment content")


def validate_attachment_upload(data: bytes) -> tuple[str, str]:
    """Validate uploaded attachment bytes (size + magic-byte type). Returns (content_type, ext).
    Raises ValueError on empty, oversize, or disallowed content."""
    if not data or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"attachment must be 1..{MAX_DOCUMENT_BYTES} bytes")
    content_type = sniff_attachment_type(data)
    return content_type, _ATTACHMENT_EXT[content_type]


def attachment_key(contact_uid: str, attachment_uid: str, ext: str) -> str:
    """Storage key for a chat attachment inside the tenant's own bucket — under the CLIENT's
    own uid, not `files/tenant/`, which carries a public-read bucket policy for branding
    assets (a different top-level segment, so this stays outside that policy's reach).

    Keyed by the ATTACHMENT's uid, not the message's. A message may carry several files (0123),
    and a message-keyed path made the second one overwrite the first. Objects written under the
    old `messages/{message_id}` key are unaffected: the key is stored on the row and read back
    from `storage_key`, never recomputed, so nothing has to be moved.
    """
    return f"files/{contact_uid}/messages/{attachment_uid}.{ext}"


def _put_tenant_object(
    bucket: str, endpoint: str, region: str | None, key: str, data: bytes, content_type: str
) -> None:
    # No ACL: private by default, and omitting it keeps this working on providers that reject
    # ACLs outright (R2).
    _client_for(endpoint, region).put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
        CacheControl="private, no-store",
    )


async def put_tenant_object(
    store: tenant_storage.TenantStore, key: str, data: bytes, content_type: str
) -> None:
    """Store a private object in a tenant's own bucket (chat attachments; distinct from the
    public `put_branding_asset` path above, which writes under the bucket's anon-read prefix)."""
    await asyncio.to_thread(
        _put_tenant_object, store.bucket, store.endpoint, store.region, key, data, content_type
    )


def _get_tenant_object(
    bucket: str, endpoint: str, region: str | None, key: str
) -> tuple[bytes, str]:
    obj = _client_for(endpoint, region).get_object(Bucket=bucket, Key=key)
    return obj["Body"].read(), obj.get("ContentType", "application/octet-stream")


async def get_tenant_object(store: tenant_storage.TenantStore, key: str) -> tuple[bytes, str]:
    """Fetch a private object's bytes + content-type from a tenant's own bucket."""
    return await asyncio.to_thread(
        _get_tenant_object, store.bucket, store.endpoint, store.region, key
    )


async def get_public_asset(session: AsyncSession, tenant_id: int, name: str) -> tuple[bytes, str]:
    """Fetch a proxied branding/content asset without exposing storage coordinates.

    The strict file-name grammar prevents path traversal and limits reads to the content-hashed
    public-asset namespace. Tenant storage is checked first; the shared legacy bucket second.
    """
    if not _PUBLIC_ASSET_NAME.fullmatch(name):
        raise ValueError("invalid asset")

    from botocore.exceptions import ClientError

    store = await tenant_storage.get_tenant_store(session, tenant_id)
    if store is not None:
        try:
            return await get_tenant_object(store, f"{TENANT_PUBLIC_PREFIX}{name}")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {
                "NoSuchKey",
                "NoSuchBucket",
                "404",
            }:
                raise

    if settings.s3_bucket:
        try:
            return await asyncio.to_thread(
                _get_document, f"tenants/{tenant_id}/{name}", settings.s3_bucket
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {
                "NoSuchKey",
                "NoSuchBucket",
                "404",
            }:
                raise
    raise FileNotFoundError(name)
