"""List SEI's client documents and store the bytes in the core bucket.

There is no firm-document service. ``POST /retrieveendclientdocumentservice/documents/list``
is per party, and ``includeFirmGenericDocs`` adds a group whose ``documentTypeDesc`` is
``Firm Generic Documents`` — packs that belong to the firm, repeated on every party's
list. Those are stored once. A client document is stored once too, with every party
whose list returned it, so a joint statement can be filed under both owners later.

The bytes go to ``sei/documents/…`` in the core bucket. Copying into a tenant bucket
and filing ``documents.*`` is ``sei_sync.publish``, which runs once the client has a
row in the book. This module stops at the core object.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# SEI's own group name. Everything else on the list is a client's.
FIRM_GENERIC = "Firm Generic Documents"

# (document_type slug, object-key feed, registry label).
_KINDS: dict[str, tuple[str, str, str]] = {
    "Statements": ("statement", "statements", "Statement"),
    "Tax Documents": ("tax", "tax", "Tax"),
    "UK Tax Documents": ("tax", "uk-tax", "Tax"),
    "Client Income and Disposal": ("tax", "income-disposal", "Tax"),
    "Transaction Advices": ("contract_note", "contract-notes", "Contract note"),
    FIRM_GENERIC: ("firm_document", "firm-generic", "Firm document"),
    "Cost and Charges Statements": ("costs_charges", "costs-charges", "Costs and charges"),
    "Fee Invoice": ("fee_invoice", "fee-invoices", "Fee invoice"),
    "Performance Reports": ("report", "performance", "Performance report"),
}

_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


class DocumentApi(Protocol):
    async def list_end_client_documents(self, body: dict[str, Any]) -> dict[str, Any]: ...

    async def retrieve_document(self, document_id: str) -> tuple[bytes, str | None]: ...


class ObjectBytes(Protocol):
    def exists(self, bucket: str, key: str) -> bool: ...

    def put(self, bucket: str, key: str, body: bytes, *, content_type: str) -> None: ...

    def get(self, bucket: str, key: str) -> bytes: ...


@dataclass(frozen=True)
class Party:
    party_id: str
    firm_id: str


@dataclass(frozen=True)
class Occurrence:
    document_id: str
    firm_id: str
    party_id: str | None
    firm_level: bool
    document_type: str
    type_label: str
    feed: str
    display_name: str
    account_number: str | None


@dataclass(frozen=True)
class DocumentBundle:
    """One physical document, however many parties' lists named it."""

    document_id: str
    firm_id: str
    firm_level: bool
    document_type: str
    type_label: str
    feed: str
    display_name: str
    party_ids: tuple[str, ...]
    account_numbers: tuple[str, ...]

    @property
    def filename(self) -> str:
        return safe_filename(self.display_name, self.document_id)


@dataclass(frozen=True)
class FetchedDocument:
    """Metadata for one object now in the core bucket."""

    document_id: str
    firm_id: str
    firm_level: bool
    document_type: str
    type_label: str
    feed: str
    display_name: str
    party_ids: tuple[str, ...]
    account_numbers: tuple[str, ...]
    bucket: str
    object_key: str
    content_type: str
    size_bytes: int
    sha256: str


def list_request(party_id: str, today: date) -> dict[str, Any]:
    """Every UK-relevant group on, US tax off.

    Dates are sent because the error catalogue refuses a list that omits them, even
    though ``required`` names only ``partyId``. ``YYYYMMDD``, and the end date is today.
    """
    return {
        "partyId": party_id,
        "startDate": "19900101",
        "endDate": today.strftime("%Y%m%d"),
        "includeStatements": True,
        "includeUKTaxDocuments": True,
        "includeContractNotesTradeAdvice": True,
        "includeFirmGenericDocs": True,
        "includeCostAndChargesStatements": True,
        "includeFeeInvoiceDocuments": True,
        "includePerformanceReports": True,
        "includeIPROAccounts": True,
        "includeUSTaxDocuments": False,
        "includeSSNForTaxDocs": False,
    }


def classify(document_type_desc: str | None) -> tuple[str, str, str, bool]:
    """SEI's group name → (slug, feed, label, firm_level)."""
    desc = (document_type_desc or "").strip()
    if desc == FIRM_GENERIC:
        slug, feed, label = _KINDS[FIRM_GENERIC]
        return slug, feed, label, True
    known = _KINDS.get(desc)
    if known:
        return (*known, False)
    slug = re.sub(r"[^a-z0-9]+", "_", desc.lower()).strip("_")[:64].strip("_") or "document"
    if slug == "other":
        slug = "document"
    return slug, slug.replace("_", "-"), (desc[:128] or "Document"), False


def flatten(payload: Mapping[str, Any], party: Party) -> list[Occurrence]:
    out: list[Occurrence] = []
    groups = payload.get("documentList") or []
    if not isinstance(groups, list):
        return out
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        slug, feed, label, firm_level = classify(_text(group.get("documentTypeDesc")))
        documents = group.get("documents") or []
        if not isinstance(documents, list):
            continue
        for raw in documents:
            if not isinstance(raw, Mapping):
                continue
            document_id = _text(raw.get("documentId"))
            if not document_id:
                continue
            out.append(
                Occurrence(
                    document_id=document_id,
                    firm_id=party.firm_id,
                    party_id=None if firm_level else party.party_id,
                    firm_level=firm_level,
                    document_type=slug,
                    type_label=label,
                    feed=feed,
                    display_name=_text(raw.get("displayName")) or f"{document_id}.pdf",
                    account_number=None if firm_level else _text(raw.get("accountNum")),
                )
            )
    return out


def bundle(occurrences: Sequence[Occurrence]) -> list[DocumentBundle]:
    """One row per ``documentId``. A firm pack is not also filed under every client."""
    grouped: dict[str, list[Occurrence]] = {}
    for item in occurrences:
        grouped.setdefault(item.document_id, []).append(item)
    bundles: list[DocumentBundle] = []
    for document_id, items in grouped.items():
        head = next((item for item in items if item.firm_level), items[0])
        client_items = () if head.firm_level else tuple(items)
        bundles.append(
            DocumentBundle(
                document_id=document_id,
                firm_id=head.firm_id,
                firm_level=head.firm_level,
                document_type=head.document_type,
                type_label=head.type_label,
                feed=head.feed,
                display_name=head.display_name,
                party_ids=tuple(
                    dict.fromkeys(item.party_id for item in client_items if item.party_id)
                ),
                account_numbers=tuple(
                    dict.fromkeys(
                        item.account_number for item in client_items if item.account_number
                    )
                ),
            )
        )
    return bundles


def core_key(item: DocumentBundle) -> str:
    """``sei/documents/{feed}/{firm}/{document_id}-{filename}``.

    No drop date. The document is immutable, and a dated key would change on a rebuild
    and leave the library pointing at yesterday's object. Same reason Seccl's tenant
    key omits the day.
    """
    return posixpath.join(
        "sei/documents",
        item.feed,
        item.firm_id,
        f"{item.document_id}-{item.filename}",
    )


def content_type(filename: str, header: str | None, body: bytes) -> str:
    if body.startswith(b"%PDF"):
        return "application/pdf"
    if header:
        media = header.split(";", 1)[0].strip().lower()
        if media and media not in ("application/octet-stream", "binary/octet-stream"):
            return media
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "pdf": "application/pdf",
        "html": "text/html",
        "htm": "text/html",
        "csv": "text/csv",
        "txt": "text/plain",
        "xml": "application/xml",
    }.get(ext, "application/octet-stream")


def safe_filename(name: str, document_id: str) -> str:
    base = posixpath.basename((name or "").replace("\\", "/")).strip()
    cleaned = _FILENAME.sub("_", base).strip("._")
    return (cleaned or f"{document_id}.pdf")[:180]


def parties_from_rows(
    rows: Sequence[Mapping[str, Any]], *, firm_ids: set[str] | None
) -> list[Party]:
    """``firm_ids is None`` lists every party. An empty set lists nobody."""
    seen: set[str] = set()
    parties: list[Party] = []
    for row in rows:
        party_id = _text(row.get("external_id") or row.get("client_id"))
        firm_id = _text(row.get("firm_id"))
        if not party_id or not firm_id or party_id in seen:
            continue
        if firm_ids is not None and firm_id not in firm_ids:
            continue
        seen.add(party_id)
        parties.append(Party(party_id=party_id, firm_id=firm_id))
    return parties


async def fetch_documents(
    *,
    parties: Sequence[Party],
    already: set[str],
    api: DocumentApi,
    store: ObjectBytes,
    core_bucket: str,
    today: date,
    concurrency: int,
) -> list[FetchedDocument]:
    """List every party, download documents not already in the warehouse.

    An unentitled list service raises. One party's 400, and one document's failure,
    are logged and skipped so the rest of the night still lands.
    """
    from sei_sync.api import DocumentServiceUnavailable, SeiApiError

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def listed(party: Party) -> list[Occurrence]:
        async with semaphore:
            payload = await api.list_end_client_documents(list_request(party.party_id, today))
        return flatten(payload, party)

    groups = await asyncio.gather(*(listed(party) for party in parties), return_exceptions=True)
    occurrences: list[Occurrence] = []
    for party, group in zip(parties, groups, strict=True):
        if isinstance(group, DocumentServiceUnavailable):
            raise group
        if isinstance(group, BaseException):
            logger.warning("sei documents: list failed for party %s: %s", party.party_id, group)
            continue
        occurrences.extend(group)

    pending = [item for item in bundle(occurrences) if item.document_id not in already]
    content_down: list[DocumentServiceUnavailable] = []

    async def one(item: DocumentBundle) -> FetchedDocument | None:
        key = core_key(item)
        try:
            async with semaphore:
                if await asyncio.to_thread(store.exists, core_bucket, key):
                    body = await asyncio.to_thread(store.get, core_bucket, key)
                    media = content_type(item.filename, None, body)
                else:
                    body, header = await api.retrieve_document(item.document_id)
                    media = content_type(item.filename, header, body)
                    await asyncio.to_thread(store.put, core_bucket, key, body, content_type=media)
        except DocumentServiceUnavailable as exc:
            content_down.append(exc)
            return None
        except SeiApiError as exc:
            logger.warning("sei documents: %s not fetched: %s", item.document_id, exc)
            return None
        except Exception as exc:  # noqa: BLE001 — one object-store fault must not drop the run
            logger.warning("sei documents: %s not stored: %s", item.document_id, exc)
            return None
        return FetchedDocument(
            document_id=item.document_id,
            firm_id=item.firm_id,
            firm_level=item.firm_level,
            document_type=item.document_type,
            type_label=item.type_label,
            feed=item.feed,
            display_name=item.display_name,
            party_ids=item.party_ids,
            account_numbers=item.account_numbers,
            bucket=core_bucket,
            object_key=key,
            content_type=media,
            size_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )

    fetched = [row for row in await asyncio.gather(*(one(item) for item in pending)) if row]
    if content_down and not fetched and pending:
        raise content_down[0]
    if content_down:
        logger.warning(
            "sei documents: content service unavailable, %d document(s) left for the next run",
            len(pending) - len(fetched),
        )
    return fetched


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
