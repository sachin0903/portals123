"""Tenant copies of the client documents ``raw.files`` has already fetched.

The bytes move from the core bucket into the tenant bucket, and a row is filed in
``documents.*``. This model is the ledger of that work, keyed by Seccl's file id and
the tenant, so a later run copies only what it has not filed.

A file whose client is not in any enabled tenant's book is left off the ledger. The
next run sees it again, which is how a document that arrived before its client was
merged gets filed once the client lands.
"""

from __future__ import annotations

import logging
from typing import Any

import pyarrow as pa
from book_sink.config import from_env
from interlace import model

from seccl_sync.files import ObjectStore
from seccl_sync.publish import (
    PostgresBook,
    already_filed,
    assignments,
    created_by_message,
    landed_files,
    publish_all,
    stamp_created,
)
from seccl_sync.settings import SecclSettings

logger = logging.getLogger("seccl_sync.models")

_SPINE: dict[str, pa.DataType] = {
    "id": pa.string(),
    "tenant_id": pa.int64(),
    "contact_id": pa.int64(),
    "bucket": pa.string(),
    "storage_key": pa.string(),
    "document_uid": pa.string(),
    "sha256": pa.string(),
    "created": pa.string(),
}


def _columns(table: Any) -> dict[str, list[Any]]:
    if table is None:
        return {}
    return {name: table.column(name).to_pylist() for name in table.column_names}


def _ledger(rows: list[dict[str, object]]) -> pa.Table:
    if not rows:
        return pa.table({name: pa.array([], type=dtype) for name, dtype in _SPINE.items()})
    return pa.table({name: [row.get(name) for row in rows] for name in _SPINE})


@model(
    name="raw.file_copies",
    strategy="merge",
    key=("id", "tenant_id"),
    depends_on=("raw.files", "raw.messages"),
    # Fifteen minutes after raw.files (30 3). A scheduled fire does not pull its ancestor,
    # so the gap still orders a night; it does run this model and its descendants.
    # depends_on orders an ad-hoc run.
    schedule={"cron": "45 3 * * *"},
    tags=("seccl", "raw"),
    description="Tenant copies of client documents; bytes in the tenant bucket, rows in documents",
)
async def raw_file_copies(this: Any, raw_files: Any, raw_messages: Any) -> pa.Table:
    files = stamp_created(
        landed_files(_columns(raw_files.table())),
        created_by_message(_columns(raw_messages.table())),
    )
    if not files:
        return _ledger([])

    done = already_filed(_columns(this.table())) if this is not None else {}
    settings = from_env()
    if not settings.book_dsn:
        logger.warning("raw.file_copies: BOOK_WRITER_DSN unset, so nothing is filed")
        return _ledger([])

    book = PostgresBook(settings.book_dsn)
    homes = book.homes(sorted({item.client_id for item in files}))
    pending = assignments(files, homes, done)
    accounted = {item.file.file_id for item in pending} | {file_id for file_id, _tenant in done}
    waiting = sum(1 for item in files if item.file_id not in accounted)
    if waiting:
        logger.info("raw.file_copies: %d documents have no client in an enabled book yet", waiting)
    if not pending:
        return _ledger([])

    filed = await publish_all(
        pending,
        copier=ObjectStore(),
        book=book,
        concurrency=SecclSettings().concurrency,
    )
    return _ledger(
        [
            {
                "id": item.file_id,
                "tenant_id": item.tenant_id,
                "contact_id": item.contact_id,
                "bucket": item.bucket,
                "storage_key": item.storage_key,
                "document_uid": item.document_uid,
                "sha256": item.sha256,
                "created": item.created,
            }
            for item in filed
        ]
    )
