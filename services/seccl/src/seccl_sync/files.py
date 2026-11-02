"""Client-document downloads: Seccl messages -> pre-signed URL -> object storage.

A message whose payload carries a file ``id`` has a downloadable document behind
it (contract notes, statements, illustrations…). The v1 pipeline walked those one
at a time onto local disk; here the whole pending set is fanned out concurrently
and the bytes land in the core bucket's raw zone, with only metadata in the
warehouse — a statement run is thousands of PDFs, which have no business in
parquet.

The document set is append-only: a message's file never changes, so a row in
``raw.files`` means "already fetched" and the model anti-joins its own previous
materialisation to pick up only what is new.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import posixpath
from dataclasses import dataclass
from typing import Any

from seccl_sync.client import SecclClient
from seccl_sync.settings import ObjectStoreSettings

logger = logging.getLogger("seccl_sync.files")

# Seccl's message_type -> the /files/{type} path segment. Only these carry a
# document; every other message type is a notification with no payload file.
MESSAGE_TYPES: dict[str, str] = {
    "ADVANCE_DIRECT_DEBIT_MANDATE_NOTICE": "advancedirectdebitmandatenotice",
    "ADVANCE_DIRECT_DEBIT_MANDATE_CONFIRMATION": "advancedirectdebitmandateconfirmation",
    "BENEFIT_CRYSTALLISATION_EVENT_STATEMENT": "bcestatement",
    "CONTRACT_NOTE": "contractnote",
    "FLEXI_ACCESS_NOTIFICATION": "flexiaccessnotification",
    "PENSION_PAYSLIP": "pensionpayslip",
    "PENSION_CANCELLATION_LETTER": "pensioncancellationletter",
    "PENSION_TRANSFER_CANCELLATION_LETTER": "pensiontransfercancellationletter",
    "PENSION_SAVINGS_STATEMENT": "pensionsavingsstatement",
    "QUARTERLY_STATEMENT": "quarterlystatement",
    "ILLUSTRATION": "illustration",
    "RETIREMENT_PACK": "retirementpack",
    "SMPI_ANNUAL_STATEMENT": "smpiannualstatement",
    "TAX_PACK": "taxpack",
    "TAX_STATEMENT": "taxstatement",
    "VALUATION_STATEMENT": "valuationstatement",
}


@dataclass(frozen=True)
class PendingFile:
    """One document to fetch: the message that referenced it plus its file id."""

    message_id: str
    message_type: str  # Seccl's enum, as landed
    path_type: str  # the /files/{type} segment
    file_id: str
    firm_id: str
    client_id: str


def pending(messages: dict[str, list[Any]], already_have: set[str]) -> list[PendingFile]:
    """Which of ``messages``' documents are not yet in ``already_have``.

    ``messages`` is the column->values mapping of the landed messages table.
    ``payload`` is a JSON string (``records_to_table`` serialises nested values),
    so the file id is read back out of it rather than off a column.
    """
    out: list[PendingFile] = []
    seen: set[str] = set()
    rows = zip(
        messages.get("id", []),
        messages.get("message_type", []),
        messages.get("client_id", []),
        messages.get("firm_id", []),
        messages.get("payload", []),
        messages.get("status", []),
        strict=False,
    )
    for message_id, message_type, client_id, firm_id, payload, status in rows:
        path_type = MESSAGE_TYPES.get(str(message_type))
        if path_type is None or status != "ACTIVE" or not payload:
            continue
        try:
            file_id = json.loads(payload).get("id")
        except (TypeError, ValueError, AttributeError):
            continue
        if not file_id or str(file_id) in already_have or str(file_id) in seen:
            continue
        seen.add(str(file_id))
        out.append(
            PendingFile(
                message_id=str(message_id),
                message_type=str(message_type),
                path_type=path_type,
                file_id=str(file_id),
                firm_id=str(firm_id or ""),
                client_id=str(client_id or ""),
            )
        )
    return out


def object_key(settings: ObjectStoreSettings, item: PendingFile, filename: str) -> str:
    """``raw/seccl/files/{firm}/{type}/{client}/{file_id}-{filename}``.

    The file id prefixes the name so two documents of the same type for the same
    client cannot collide on Seccl's (non-unique) filenames.
    """
    return posixpath.join(
        settings.prefix.strip("/"),
        item.firm_id or "unknown",
        item.path_type,
        item.client_id or "unknown",
        f"{item.file_id}-{filename}",
    )


class ObjectStore:
    """Minimal S3 put/exists over boto3, driven from a worker thread.

    boto3 is synchronous; every call is handed to a thread so the download fan-out
    is never blocked by an upload.
    """

    def __init__(self, settings: ObjectStoreSettings | None = None) -> None:
        self.settings = settings or ObjectStoreSettings()

    def _client(self) -> Any:
        import boto3  # imported lazily: model discovery must not need AWS libs

        if not hasattr(self, "_cached"):
            self._cached = boto3.client(
                "s3",
                endpoint_url=self.settings.endpoint_url if self.settings.endpoint else None,
                aws_access_key_id=self.settings.access_key_id or None,
                aws_secret_access_key=self.settings.secret_access_key or None,
                region_name=self.settings.region,
            )
        return self._cached

    async def put(self, key: str, body: bytes, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        await asyncio.to_thread(
            lambda: self._client().put_object(
                Bucket=self.settings.bucket, Key=key, Body=body, **extra
            )
        )

    async def copy(self, source_key: str, dest_bucket: str, dest_key: str) -> None:
        """Server-side copy from the core bucket into a tenant bucket.

        Same endpoint, same credential: the platform root key that wrote the raw object
        also provisions tenant buckets, so it can read one and write the other. The
        bytes never come back through this process.
        """
        source = {"Bucket": self.settings.bucket, "Key": source_key}

        def _copy() -> None:
            self._client().copy_object(Bucket=dest_bucket, Key=dest_key, CopySource=source)

        await asyncio.to_thread(_copy)

    async def delete(self, bucket: str, key: str) -> None:
        def _delete() -> None:
            self._client().delete_object(Bucket=bucket, Key=key)

        await asyncio.to_thread(_delete)


async def fetch_all(
    items: list[PendingFile],
    *,
    api: SecclClient,
    store: ObjectStore,
) -> list[dict[str, Any]]:
    """Mint a URL, download, and upload every pending document, all concurrently.

    Concurrency is bounded by the API client's own semaphore (``SECCL_CONCURRENCY``),
    which every Seccl call and every pre-signed download passes through — so this
    fans out over the whole pending set without a second knob to tune.

    A document that fails is logged and skipped, not fatal: one broken file must
    not strand the rest of a nightly statement run. The keyed merge means it is
    retried on the next pass.
    """

    async def one(item: PendingFile) -> dict[str, Any] | None:
        try:
            url = await api.file_url(
                item.path_type,
                {"id": item.file_id, "firmId": item.firm_id, "clientId": item.client_id},
            )
            body = await api.download(url)
            filename = posixpath.basename(url.split("?")[0]) or f"{item.file_id}.pdf"
            key = object_key(store.settings, item, filename)
            await store.put(key, body, content_type="application/pdf")
        except Exception as exc:  # noqa: BLE001 - one bad document must not fail the run
            logger.warning("raw.files: %s (%s) failed: %s", item.file_id, item.path_type, exc)
            return None
        return {
            "id": item.file_id,
            "messageId": item.message_id,
            "firmId": item.firm_id,
            "clientId": item.client_id,
            "messageType": item.message_type,
            "fileType": item.path_type,
            "bucket": store.settings.bucket,
            "objectKey": key,
            "filename": filename,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }

    results = await asyncio.gather(*(one(item) for item in items))
    return [record for record in results if record is not None]
