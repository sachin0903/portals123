"""Tenant copies of the documents ``raw.client_documents`` has already fetched.

The bytes move from the core bucket into the tenant bucket, and a row is filed in
``documents.*``. Keyed by SEI's document id, the tenant and the contact, so a later
run copies only what it has not filed. A firm pack uses contact id 0: there is no
client, and the library row is filed with a null contact.

A client document whose party is not in any enabled tenant's book is left off the
ledger. The next run sees it again, which is how a document that arrived before its
client was merged gets filed once the client lands.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pyarrow as pa
from book_sink.config import from_env
from interlace import model

from sei_sync.objects import client as s3_client
from sei_sync.objects import copy as s3_copy
from sei_sync.publish import (
    FIRM_CONTACT,
    PostgresBook,
    already_filed,
    assignments,
    landed_documents,
    publish_all,
)
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.models")

SCHEMA = pa.schema(
    [
        pa.field("document_id", pa.string(), nullable=False),
        pa.field("tenant_id", pa.int64(), nullable=False),
        pa.field("contact_id", pa.int64(), nullable=False),
        pa.field("bucket", pa.string(), nullable=False),
        pa.field("storage_key", pa.string(), nullable=False),
        pa.field("document_uid", pa.string()),
        pa.field("sha256", pa.string()),
    ]
)


class _Copier:
    """Server-side copy from the core bucket into a tenant bucket."""

    def __init__(self, settings: SeiSettings) -> None:
        self._settings = settings
        self._s3: Any = None

    def _client(self) -> Any:
        if self._s3 is None:
            self._s3 = s3_client(self._settings)
        return self._s3

    async def copy(self, source_key: str, dest_bucket: str, dest_key: str) -> None:
        source_bucket = self._settings.core_bucket

        def _copy() -> None:
            s3_copy(self._client(), source_bucket, source_key, dest_bucket, dest_key)

        await asyncio.to_thread(_copy)


def _columns(table: Any) -> dict[str, list[Any]]:
    if table is None:
        return {}
    return {name: table.column(name).to_pylist() for name in table.column_names}


def _ledger(rows: list[dict[str, object]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=SCHEMA)


@model(
    name="raw.document_copies",
    strategy="merge",
    key=("document_id", "tenant_id", "contact_id"),
    depends_on=("raw.client_documents",),
    # Fifteen minutes after the fetch (50 7). A scheduled fire does not pull ancestors;
    # it does run this model and everything downstream of it.
    schedule={"cron": "5 8 * * *"},
    tags=("sei", "raw", "api"),
    description="Tenant copies of SEI documents; bytes in the tenant bucket, rows in documents",
)
async def raw_document_copies(this: Any, raw_client_documents: Any) -> pa.Table:
    documents = landed_documents(_columns(raw_client_documents.table()))
    if not documents:
        return _ledger([])

    done = already_filed(_columns(this.table())) if this is not None else set()
    settings = from_env()
    if not settings.book_dsn:
        logger.warning("sei raw.document_copies: BOOK_WRITER_DSN unset, so nothing is filed")
        return _ledger([])

    book = PostgresBook(settings.book_dsn)
    parties = sorted({party for document in documents for party in document.party_ids})
    firms = sorted({document.firm_id for document in documents if document.firm_level})
    pending = assignments(
        documents,
        book.client_homes(parties),
        book.firm_homes(firms),
        done,
    )
    if not pending:
        waiting = _waiting(documents, done)
        if waiting:
            logger.info(
                "sei raw.document_copies: %d client document(s) waiting on a merged party",
                waiting,
            )
        return _ledger([])

    filed = await publish_all(
        pending,
        copier=_Copier(SeiSettings()),
        book=book,
        concurrency=SeiSettings().api_concurrency,
    )
    return _ledger(
        [
            {
                "document_id": item.document_id,
                "tenant_id": item.tenant_id,
                "contact_id": item.contact_id,
                "bucket": item.bucket,
                "storage_key": item.storage_key,
                "document_uid": item.document_uid,
                "sha256": item.sha256,
            }
            for item in filed
        ]
    )


def _waiting(documents: list[Any], done: set[tuple[str, int, int]]) -> int:
    filed_ids = {document_id for document_id, _tenant, contact in done if contact != FIRM_CONTACT}
    return sum(
        1
        for document in documents
        if not document.firm_level and document.document_id not in filed_ids
    )
