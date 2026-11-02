"""Copy client documents from the core bucket into each tenant's bucket, and file them.

``raw.files`` has already fetched the PDF into the core bucket. This is the grant-filtered
half of that: a copy into the tenant bucket at
``files/provider/seccl/{type}/{client}/{name}`` (docs/storage.md), plus a
``documents.documents`` row so the library can show it.

Seccl has no firm document library. Every PDF the collection documents is a client message
(``GET /message/{firm}`` then ``POST /files/{type}`` with a ``clientId``). The firm-scoped
calls are not documents a client is shown: ``POST /data/accounts`` is an accounts CSV, and
``POST /files/drawdownapplication`` is a blank form. Transfer filestore calls are uploads
we send to Seccl.

A file is filed only for a tenant whose book already holds that client (``platform.links``).
One that arrives before its client is merged is left pending and retried next run, rather
than filed against nobody.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Protocol

logger = logging.getLogger("seccl_sync.publish")

_PROVIDER = "seccl"

# Seccl's /files/{type} segment -> (documents.document_type slug, picker label).
# Slugs stay inside documents.document_types' ``^[a-z0-9_]+$`` check. A type we have
# not named falls through to the segment itself, which is already that shape.
_DOCUMENT_TYPES: dict[str, tuple[str, str]] = {
    "advancedirectdebitmandateconfirmation": ("letter", "Letter"),
    "advancedirectdebitmandatenotice": ("letter", "Letter"),
    "bcestatement": ("statement", "Statement"),
    "contractnote": ("contract_note", "Contract note"),
    "flexiaccessnotification": ("letter", "Letter"),
    "illustration": ("illustration", "Illustration"),
    "pensioncancellationletter": ("letter", "Letter"),
    "pensionpayslip": ("payslip", "Payslip"),
    "pensionsavingsstatement": ("statement", "Statement"),
    "pensiontransfercancellationletter": ("letter", "Letter"),
    "quarterlystatement": ("statement", "Statement"),
    "retirementpack": ("letter", "Letter"),
    "smpiannualstatement": ("statement", "Statement"),
    "taxpack": ("tax", "Tax"),
    "taxstatement": ("tax", "Tax"),
    "valuationstatement": ("statement", "Statement"),
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Which clients of this firm live in which tenant's book, and where that tenant's
# bucket is. A paused or unprovisioned tenant is absent on purpose: there is nowhere
# to put the copy, and filing a row that points at a missing bucket 404s on download.
_HOMES = """
SELECT l.external_id, l.tenant_id, l.entity_id, ts.bucket
  FROM platform.links l
  JOIN storage.tenants ts
    ON ts.tenant_id = l.tenant_id AND ts.status = 'active'
  JOIN platform.tenant_platforms tp
    ON tp.tenant_id = l.tenant_id
   AND tp.provider = 'seccl'
   AND tp.enabled
   AND tp.refresh_paused_at IS NULL
 WHERE l.provider = 'seccl'
   AND l.entity_type = 'contact'
   AND l.link_type = 'primary'
   AND l.external_id = ANY(%s)
"""

_INGEST = """
SELECT documents.ingest_provider_document(
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS jsonb)
)
"""


@dataclass(frozen=True)
class LandedFile:
    """One PDF already in the core bucket."""

    file_id: str
    message_id: str
    firm_id: str
    client_id: str
    file_type: str
    object_key: str
    filename: str
    size_bytes: int
    sha256: str
    #: Seccl message ``created``, canonical UTC, when the message has one.
    created: str | None = None


@dataclass(frozen=True)
class ClientHome:
    """A tenant that holds this Seccl client, and the bucket its copy belongs in."""

    tenant_id: int
    contact_id: int
    bucket: str


@dataclass(frozen=True)
class Assignment:
    """One copy to make: this file, into this tenant."""

    file: LandedFile
    home: ClientHome
    storage_key: str
    document_type: str
    type_label: str
    name: str
    #: The key a previous run filed, when this copy is replacing it.
    previous_key: str | None = None


@dataclass(frozen=True)
class FiledCopy:
    """What the warehouse ledger records after a copy has been filed."""

    file_id: str
    tenant_id: int
    contact_id: int
    bucket: str
    storage_key: str
    document_uid: str
    sha256: str
    created: str | None = None


class Copier(Protocol):
    async def copy(self, source_key: str, dest_bucket: str, dest_key: str) -> None: ...

    async def delete(self, bucket: str, key: str) -> None: ...


class Book(Protocol):
    def homes(self, client_ids: list[str]) -> dict[str, list[ClientHome]]: ...

    def ingest(self, item: Assignment) -> str: ...


def canonical_created(value: Any) -> str | None:
    """A message ``created`` value as a UTC timestamp string, or None when it is not one.

    The warehouse stores the field as text (``2024-11-03T08:50:30.349Z`` and the
    offset form both occur). One spelling is what lets the ledger remember that
    this date has already been written, instead of re-filing the document nightly.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=UTC)
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _segment(value: str, fallback: str) -> str:
    cleaned = _SAFE.sub("-", value).strip("-.")
    return cleaned or fallback


def classify(file_type: str) -> tuple[str, str]:
    """``(slug, label)`` for a Seccl file type."""
    known = _DOCUMENT_TYPES.get(file_type)
    if known is not None:
        return known
    slug = _segment(file_type.lower(), "document").replace("-", "_").replace(".", "")
    return slug or "document", (slug or "document").replace("_", " ").capitalize()


def document_name(filename: str, label: str) -> str:
    """The library name: Seccl's own filename, or the type label when that is just an id."""
    stem = posixpath.splitext(posixpath.basename(filename))[0].strip()
    if stem and not stem.isdigit():
        return stem
    return label


def object_filename(file_id: str, filename: str) -> str:
    """Seccl's filename, with the file id prefixed only when that name is not already the id.

    The pre-signed URL's name is the file id (``0000FLMHS.pdf``,
    ``6463a22a…-1730619030349.pdf``). Prefixing it again writes the id twice.
    A generic name such as ``note.pdf`` still takes the prefix, so two documents
    that share it do not collide.
    """
    safe_id = _segment(file_id, "file")
    stem, ext = posixpath.splitext(posixpath.basename(filename.replace("\\", "/")))
    suffix = ext.lower() if re.fullmatch(r"\.[A-Za-z0-9]+", ext) else ".pdf"
    safe_stem = _segment(stem, "")
    if not safe_stem:
        return f"{safe_id}{suffix}"
    if safe_stem == safe_id or safe_stem.startswith(f"{safe_id}-"):
        return f"{safe_stem}{suffix}"
    return f"{safe_id}-{safe_stem}{suffix}"


def tenant_object_key(
    *,
    file_type: str,
    client_id: str,
    file_id: str,
    filename: str,
) -> str:
    """``files/provider/seccl/{type}/{client}/{name}``.

    No drop date. A contract note is immutable and the day we copied it is not a
    property of it; a dated key would change on a rebuild and leave the library
    pointing at yesterday's object.
    """
    return posixpath.join(
        "files/provider/seccl",
        _segment(file_type, "document"),
        _segment(client_id, "unknown"),
        object_filename(file_id, filename),
    )


def _column(columns: dict[str, list[Any]], name: str, n: int) -> list[Any]:
    """One column, padded so a missing optional field does not drop every row."""
    values = list(columns.get(name, []))
    if len(values) < n:
        values.extend([None] * (n - len(values)))
    return values[:n]


def landed_files(columns: dict[str, list[Any]]) -> list[LandedFile]:
    """Rows of ``raw.files`` that have everything a copy needs.

    A row missing its key, hash or client is skipped: filing it would point the
    library at bytes we cannot name. Optional columns (the message, the firm,
    the filename) may be absent; they are not what the copy is named by.
    """
    n = len(columns.get("id", []))
    rows = zip(
        _column(columns, "id", n),
        _column(columns, "message_id", n),
        _column(columns, "firm_id", n),
        _column(columns, "client_id", n),
        _column(columns, "file_type", n),
        _column(columns, "object_key", n),
        _column(columns, "filename", n),
        _column(columns, "bytes", n),
        _column(columns, "sha256", n),
        _column(columns, "created", n),
        strict=True,
    )
    out: list[LandedFile] = []
    for (
        file_id,
        message_id,
        firm_id,
        client_id,
        file_type,
        object_key,
        filename,
        size,
        sha,
        created,
    ) in rows:
        if not file_id or not client_id or not object_key or not sha or not file_type:
            continue
        out.append(
            LandedFile(
                file_id=str(file_id),
                message_id=str(message_id or ""),
                firm_id=str(firm_id or ""),
                client_id=str(client_id),
                file_type=str(file_type),
                object_key=str(object_key),
                filename=str(filename or f"{file_id}.pdf"),
                size_bytes=int(size or 0),
                sha256=str(sha),
                created=canonical_created(created),
            )
        )
    return out


def created_by_message(columns: dict[str, list[Any]]) -> dict[str, str]:
    """Message id → canonical ``created``. A message with no parseable time is absent."""
    n = len(columns.get("id", []))
    found: dict[str, str] = {}
    for message_id, created in zip(
        _column(columns, "id", n),
        _column(columns, "created", n),
        strict=True,
    ):
        stamp = canonical_created(created)
        if message_id and stamp:
            found[str(message_id)] = stamp
    return found


def stamp_created(files: list[LandedFile], by_message: dict[str, str]) -> list[LandedFile]:
    """Prefer the message clock. A file row's own ``created`` is the fallback."""
    stamped: list[LandedFile] = []
    for item in files:
        created = by_message.get(item.message_id) or item.created
        stamped.append(item if created == item.created else replace(item, created=created))
    return stamped


def assignments(
    files: list[LandedFile],
    homes: dict[str, list[ClientHome]],
    already: dict[tuple[str, int], tuple[str, str | None]],
) -> list[Assignment]:
    """Which copies are still outstanding.

    ``already`` maps ``(file id, tenant id)`` to ``(storage key, created)``. A pair
    whose key is already the name we would write, and whose created time is already
    the message time, is skipped. A doubled name is copied again. A pair filed at
    the copy time is filed again so the library date can move, without a second
    copy of the bytes. A client with no home is not a failure — the client has not
    been merged into a book yet.
    """
    out: list[Assignment] = []
    for item in files:
        for home in homes.get(item.client_id, []):
            storage_key = tenant_object_key(
                file_type=item.file_type,
                client_id=item.client_id,
                file_id=item.file_id,
                filename=item.filename,
            )
            previous = already.get((item.file_id, home.tenant_id))
            prev_key, prev_created = previous if previous is not None else (None, None)
            if prev_key == storage_key and (item.created is None or prev_created == item.created):
                continue
            slug, label = classify(item.file_type)
            out.append(
                Assignment(
                    file=item,
                    home=home,
                    storage_key=storage_key,
                    document_type=slug,
                    type_label=label,
                    name=document_name(item.filename, label),
                    previous_key=prev_key or None,
                )
            )
    return out


def already_filed(columns: dict[str, list[Any]]) -> dict[tuple[str, int], tuple[str, str | None]]:
    """``(file id, tenant id)`` → ``(storage key, created)`` a previous run filed."""
    n = len(columns.get("id", []))
    done: dict[tuple[str, int], tuple[str, str | None]] = {}
    rows = zip(
        _column(columns, "id", n),
        _column(columns, "tenant_id", n),
        _column(columns, "storage_key", n),
        _column(columns, "created", n),
        strict=True,
    )
    for file_id, tenant_id, storage_key, created in rows:
        if file_id is None or tenant_id is None:
            continue
        done[(str(file_id), int(tenant_id))] = (
            str(storage_key or ""),
            canonical_created(created),
        )
    return done


class PostgresBook:
    """The book half: which tenants hold a client, and the ingest function that files it.

    One connection for the run. ``book_writer`` may call ``documents.ingest_provider_document``
    and nothing else in that schema — the function is what keeps a pipeline credential
    from reading a client's identity documents.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def homes(self, client_ids: list[str]) -> dict[str, list[ClientHome]]:
        if not client_ids:
            return {}
        import psycopg

        found: dict[str, list[ClientHome]] = {}
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(_HOMES, (client_ids,))
            for external_id, tenant_id, contact_id, bucket in cur.fetchall():
                found.setdefault(str(external_id), []).append(
                    ClientHome(
                        tenant_id=int(tenant_id), contact_id=int(contact_id), bucket=str(bucket)
                    )
                )
        return found

    def ingest(self, item: Assignment) -> str:
        import json

        import psycopg

        metadata = {
            "provider": _PROVIDER,
            "external_id": item.file.file_id,
            "message_id": item.file.message_id,
            "firm_id": item.file.firm_id,
            "client_id": item.file.client_id,
            "file_type": item.file.file_type,
        }
        if item.file.created:
            metadata["created"] = item.file.created
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                _INGEST,
                (
                    item.home.tenant_id,
                    item.home.contact_id,
                    item.home.bucket,
                    item.storage_key,
                    "application/pdf",
                    item.file.size_bytes,
                    item.file.sha256,
                    item.document_type,
                    item.type_label,
                    item.name,
                    _PROVIDER,
                    item.file.file_id,
                    json.dumps(metadata),
                ),
            )
            row = cur.fetchone()
            conn.commit()
        if row is None or row[0] is None:
            raise RuntimeError(f"ingest returned no document uid for {item.file.file_id}")
        return str(row[0])


async def publish_all(
    pending: list[Assignment],
    *,
    copier: Copier,
    book: Book,
    concurrency: int,
) -> list[FiledCopy]:
    """Copy then file every assignment. One failure is logged and skipped, not fatal.

    The copy happens before the database write, so a failed insert leaves an object
    the next run overwrites and then files. The ledger only records what was filed,
    which is what makes the skip safe.
    """
    limit = max(concurrency, 1)
    gate = asyncio.Semaphore(limit)

    async def copy_one(item: Assignment) -> Assignment | None:
        # The object is already at the right key. This pass only moves the library
        # date onto the message's created time, so the bytes stay where they are.
        if item.previous_key == item.storage_key:
            return item
        async with gate:
            try:
                await copier.copy(item.file.object_key, item.home.bucket, item.storage_key)
            except Exception as exc:  # noqa: BLE001 - one bad object must not fail the run
                logger.warning(
                    "raw.file_copies: copy %s -> tenant %s failed: %s",
                    item.file.file_id,
                    item.home.tenant_id,
                    exc,
                )
                return None
        return item

    copied = [item for item in await asyncio.gather(*(copy_one(item) for item in pending)) if item]

    filed: list[FiledCopy] = []
    for item in copied:
        try:
            uid = await asyncio.to_thread(book.ingest, item)
        except Exception as exc:  # noqa: BLE001 - same rule as the copy
            logger.warning(
                "raw.file_copies: file %s for tenant %s failed: %s",
                item.file.file_id,
                item.home.tenant_id,
                exc,
            )
            continue
        if item.previous_key and item.previous_key != item.storage_key:
            try:
                await copier.delete(item.home.bucket, item.previous_key)
            except Exception as exc:  # noqa: BLE001 - the new object is filed; retry the removal
                logger.warning(
                    "raw.file_copies: remove doubled key %s for %s failed: %s",
                    item.previous_key,
                    item.file.file_id,
                    exc,
                )
                continue
        filed.append(
            FiledCopy(
                file_id=item.file.file_id,
                tenant_id=item.home.tenant_id,
                contact_id=item.home.contact_id,
                bucket=item.home.bucket,
                storage_key=item.storage_key,
                document_uid=uid,
                sha256=item.file.sha256,
                created=item.file.created,
            )
        )
    return filed
