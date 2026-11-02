"""Copy SEI documents from the core bucket into each tenant's bucket, and file them.

``raw.client_documents`` has already fetched the bytes. This is the grant-filtered
half: a copy at ``files/provider/sei/{feed}/{document}-{name}``, plus a
``documents.documents`` row.

A client document is filed only for a tenant whose book already holds that party
(``platform.links``). One that arrives before its client is merged is left pending.
A firm pack has no party: it is filed, with no subject link, for every enabled
tenant of that SEI firm.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger("sei_sync.publish")

_PROVIDER = "sei"

# Sentinel in the copy ledger only. crm.contacts identity starts at 1, so 0 is not
# a contact: it marks a firm pack, which is filed with a NULL contact.
FIRM_CONTACT = 0

_CLIENT_HOMES = """
SELECT l.external_id, l.tenant_id, l.entity_id, ts.bucket
  FROM platform.links l
  JOIN storage.tenants ts
    ON ts.tenant_id = l.tenant_id AND ts.status = 'active'
  JOIN platform.tenant_platforms tp
    ON tp.tenant_id = l.tenant_id
   AND tp.provider = 'sei'
   AND tp.enabled
   AND tp.refresh_paused_at IS NULL
 WHERE l.provider = 'sei'
   AND l.entity_type = 'contact'
   AND l.link_type = 'primary'
   AND l.external_id = ANY(%s)
"""

_FIRM_HOMES = """
SELECT tp.config ->> 'firm_id', tp.tenant_id, ts.bucket
  FROM platform.tenant_platforms tp
  JOIN storage.tenants ts
    ON ts.tenant_id = tp.tenant_id AND ts.status = 'active'
 WHERE tp.provider = 'sei'
   AND tp.enabled
   AND tp.refresh_paused_at IS NULL
   AND tp.config ->> 'firm_id' = ANY(%s)
"""

_INGEST = """
SELECT documents.ingest_provider_document(
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
)
"""


@dataclass(frozen=True)
class LandedDocument:
    document_id: str
    firm_id: str
    firm_level: bool
    document_type: str
    type_label: str
    feed: str
    display_name: str
    party_ids: tuple[str, ...]
    account_numbers: tuple[str, ...]
    object_key: str
    content_type: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ClientHome:
    tenant_id: int
    contact_id: int
    bucket: str


@dataclass(frozen=True)
class FirmHome:
    tenant_id: int
    bucket: str


@dataclass(frozen=True)
class Assignment:
    document: LandedDocument
    tenant_id: int
    contact_id: int | None
    bucket: str
    storage_key: str


@dataclass(frozen=True)
class FiledCopy:
    document_id: str
    tenant_id: int
    contact_id: int
    bucket: str
    storage_key: str
    document_uid: str
    sha256: str


class Copier(Protocol):
    async def copy(self, source_key: str, dest_bucket: str, dest_key: str) -> None: ...


class Book(Protocol):
    def client_homes(self, party_ids: list[str]) -> dict[str, list[ClientHome]]: ...

    def firm_homes(self, firm_ids: list[str]) -> dict[str, list[FirmHome]]: ...

    def ingest(self, item: Assignment) -> str: ...


def tenant_object_key(document: LandedDocument) -> str:
    """``files/provider/sei/{feed}/{document_id}-{filename}``.

    One key per document. A joint statement is one object with two subject links, so
    the key cannot name one of the owners. No drop date: the document is immutable,
    and a dated key would move on a rebuild while the library row still named the old one.
    """
    filename = posixpath.basename(document.display_name.replace("\\", "/")) or (
        f"{document.document_id}.pdf"
    )
    return posixpath.join(
        "files/provider/sei",
        document.feed,
        f"{document.document_id}-{filename}",
    )


def landed_documents(columns: dict[str, list[Any]]) -> list[LandedDocument]:
    rows = zip(
        columns.get("document_id", []),
        columns.get("firm_id", []),
        columns.get("firm_level", []),
        columns.get("document_type", []),
        columns.get("type_label", []),
        columns.get("feed", []),
        columns.get("display_name", []),
        columns.get("party_ids", []),
        columns.get("account_numbers", []),
        columns.get("object_key", []),
        columns.get("content_type", []),
        columns.get("bytes", []),
        columns.get("sha256", []),
        strict=False,
    )
    out: list[LandedDocument] = []
    for (
        document_id,
        firm_id,
        firm_level,
        document_type,
        type_label,
        feed,
        display_name,
        party_ids,
        account_numbers,
        object_key,
        content_type,
        size,
        sha,
    ) in rows:
        if not document_id or not firm_id or not object_key or not sha or not feed:
            continue
        out.append(
            LandedDocument(
                document_id=str(document_id),
                firm_id=str(firm_id),
                firm_level=bool(firm_level),
                document_type=str(document_type or "document"),
                type_label=str(type_label or "Document"),
                feed=str(feed),
                display_name=str(display_name or document_id),
                party_ids=tuple(str(party) for party in (party_ids or []) if party),
                account_numbers=tuple(
                    str(account) for account in (account_numbers or []) if account
                ),
                object_key=str(object_key),
                content_type=str(content_type or "application/octet-stream"),
                size_bytes=int(size or 0),
                sha256=str(sha),
            )
        )
    return out


def already_filed(columns: dict[str, list[Any]]) -> set[tuple[str, int, int]]:
    """``(document id, tenant id, contact id)``. Firm packs use contact id 0."""
    done: set[tuple[str, int, int]] = set()
    for document_id, tenant_id, contact_id in zip(
        columns.get("document_id", []),
        columns.get("tenant_id", []),
        columns.get("contact_id", []),
        strict=False,
    ):
        if document_id is None or tenant_id is None or contact_id is None:
            continue
        done.add((str(document_id), int(tenant_id), int(contact_id)))
    return done


def assignments(
    documents: list[LandedDocument],
    client_homes: dict[str, list[ClientHome]],
    firm_homes: dict[str, list[FirmHome]],
    already: set[tuple[str, int, int]],
) -> list[Assignment]:
    """Copies still outstanding. A client with no home waits; that is not a failure."""
    out: list[Assignment] = []
    for document in documents:
        if document.firm_level:
            for firm in firm_homes.get(document.firm_id, []):
                if (document.document_id, firm.tenant_id, FIRM_CONTACT) in already:
                    continue
                out.append(
                    Assignment(
                        document=document,
                        tenant_id=firm.tenant_id,
                        contact_id=None,
                        bucket=firm.bucket,
                        storage_key=tenant_object_key(document),
                    )
                )
            continue
        seen_contacts: set[tuple[int, int]] = set()
        for party_id in document.party_ids:
            for client in client_homes.get(party_id, []):
                if (client.tenant_id, client.contact_id) in seen_contacts:
                    continue
                if (document.document_id, client.tenant_id, client.contact_id) in already:
                    continue
                seen_contacts.add((client.tenant_id, client.contact_id))
                out.append(
                    Assignment(
                        document=document,
                        tenant_id=client.tenant_id,
                        contact_id=client.contact_id,
                        bucket=client.bucket,
                        storage_key=tenant_object_key(document),
                    )
                )
    return out


class PostgresBook:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def client_homes(self, party_ids: list[str]) -> dict[str, list[ClientHome]]:
        if not party_ids:
            return {}
        import psycopg

        found: dict[str, list[ClientHome]] = {}
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(_CLIENT_HOMES, (party_ids,))
            for external_id, tenant_id, contact_id, bucket in cur.fetchall():
                found.setdefault(str(external_id), []).append(
                    ClientHome(
                        tenant_id=int(tenant_id), contact_id=int(contact_id), bucket=str(bucket)
                    )
                )
        return found

    def firm_homes(self, firm_ids: list[str]) -> dict[str, list[FirmHome]]:
        if not firm_ids:
            return {}
        import psycopg

        found: dict[str, list[FirmHome]] = {}
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(_FIRM_HOMES, (firm_ids,))
            for firm_id, tenant_id, bucket in cur.fetchall():
                found.setdefault(str(firm_id), []).append(
                    FirmHome(tenant_id=int(tenant_id), bucket=str(bucket))
                )
        return found

    def ingest(self, item: Assignment) -> str:
        import json

        import psycopg

        metadata = {
            "provider": _PROVIDER,
            "external_id": item.document.document_id,
            "firm_id": item.document.firm_id,
            "firm_level": item.document.firm_level,
            "feed": item.document.feed,
            "account_numbers": list(item.document.account_numbers),
            "core_key": item.document.object_key,
        }
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(
                _INGEST,
                (
                    item.tenant_id,
                    item.contact_id,
                    item.bucket,
                    item.storage_key,
                    item.document.content_type,
                    item.document.size_bytes,
                    item.document.sha256,
                    item.document.document_type,
                    item.document.type_label,
                    item.document.display_name,
                    _PROVIDER,
                    item.document.document_id,
                    json.dumps(metadata),
                ),
            )
            row = cur.fetchone()
        if row is None or row[0] is None:
            raise RuntimeError(f"ingest returned no document uid for {item.document.document_id}")
        return str(row[0])


async def publish_all(
    pending: list[Assignment],
    *,
    copier: Copier,
    book: Book,
    concurrency: int,
) -> list[FiledCopy]:
    """Copy, then file. One failure is logged and left for the next run."""
    gate = asyncio.Semaphore(max(concurrency, 1))

    async def copy_one(item: Assignment) -> Assignment | None:
        async with gate:
            try:
                await copier.copy(item.document.object_key, item.bucket, item.storage_key)
            except Exception as exc:  # noqa: BLE001 — one bad object must not fail the run
                logger.warning(
                    "sei documents: copy %s -> tenant %s failed: %s",
                    item.document.document_id,
                    item.tenant_id,
                    exc,
                )
                return None
        return item

    copied = [item for item in await asyncio.gather(*(copy_one(item) for item in pending)) if item]
    filed: list[FiledCopy] = []
    for item in copied:
        try:
            uid = await asyncio.to_thread(book.ingest, item)
        except Exception as exc:  # noqa: BLE001 — same rule as the copy
            logger.warning(
                "sei documents: file %s for tenant %s failed: %s",
                item.document.document_id,
                item.tenant_id,
                exc,
            )
            continue
        filed.append(
            FiledCopy(
                document_id=item.document.document_id,
                tenant_id=item.tenant_id,
                contact_id=item.contact_id if item.contact_id is not None else FIRM_CONTACT,
                bucket=item.bucket,
                storage_key=item.storage_key,
                document_uid=uid,
                sha256=item.document.sha256,
            )
        )
    return filed
