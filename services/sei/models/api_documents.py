"""``raw.client_documents`` — SEI statements and the firm pack, bytes in the core bucket.

One row per SEI ``documentId``. The bytes are not in this table: they are in the core
bucket under ``sei/documents/…``. ``raw.document_copies`` copies each object into the
tenant buckets that may see it and files the library row.

Firm documents are not a second feed. The list call is per client, and
``includeFirmGenericDocs`` returns a group named ``Firm Generic Documents``. Those are
stored once (``firm_level``) with no party. A client document records every party whose
list returned it.

Disabled when ``SEI_API_BASE_URL`` is blank: an empty typed table, and ``merge`` leaves
whatever a previous run stored. The list and content paths are all-lowercase; a camelCase
service name 404s ``Unable to identify proxy`` and fails the run rather than looking like
an empty book.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast

import pyarrow as pa
from interlace import model

from sei_sync.api import SeiApi
from sei_sync.documents import FetchedDocument, fetch_documents, parties_from_rows
from sei_sync.firms import resolve_firm_ids
from sei_sync.objects import client as s3_client
from sei_sync.objects import exists as s3_exists
from sei_sync.objects import get as s3_get
from sei_sync.objects import put as s3_put
from sei_sync.previous import previous_table
from sei_sync.settings import SeiSettings

logger = logging.getLogger("sei_sync.models")

SCHEMA = pa.schema(
    [
        pa.field("document_id", pa.string(), nullable=False),
        pa.field("firm_id", pa.string(), nullable=False),
        pa.field("firm_level", pa.bool_(), nullable=False),
        pa.field("document_type", pa.string(), nullable=False),
        pa.field("type_label", pa.string()),
        pa.field("feed", pa.string(), nullable=False),
        pa.field("display_name", pa.string()),
        pa.field("party_ids", pa.list_(pa.string())),
        pa.field("account_numbers", pa.list_(pa.string())),
        pa.field("bucket", pa.string(), nullable=False),
        pa.field("object_key", pa.string(), nullable=False),
        pa.field("content_type", pa.string()),
        pa.field("bytes", pa.int64()),
        pa.field("sha256", pa.string()),
        pa.field("fetched_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)


class _Store:
    def __init__(self, settings: SeiSettings) -> None:
        self._s3 = s3_client(settings)

    def exists(self, bucket: str, key: str) -> bool:
        return s3_exists(self._s3, bucket, key)

    def put(self, bucket: str, key: str, body: bytes, *, content_type: str) -> None:
        s3_put(self._s3, bucket, key, body, content_type=content_type)

    def get(self, bucket: str, key: str) -> bytes:
        return s3_get(self._s3, bucket, key)


@model(
    name="raw.client_documents",
    strategy="merge",
    key=("document_id",),
    depends_on=("platform.clients", "ref.firms"),
    # After the drop has rebuilt platform.clients (05:30). A scheduled fire does not pull
    # ancestors, so this gap still orders the night; it does run this model and everything
    # downstream of it.
    schedule={"cron": "50 7 * * *"},
    tags=("sei", "raw", "api"),
    description="SEI client and firm documents, bytes in the core bucket",
)
async def raw_client_documents(this: Any, platform_clients: Any, ref_firms: Any = None) -> pa.Table:
    settings = SeiSettings()
    now = datetime.now(tz=UTC)
    if not settings.api_base_url:
        logger.info("sei raw.client_documents: SEI_API_BASE_URL unset — API sync disabled")
        return pa.Table.from_pylist([], schema=SCHEMA)

    parties = parties_from_rows(_rows(platform_clients), firm_ids=_wanted(ref_firms))
    if not parties:
        logger.warning("sei raw.client_documents: no clients to list; is platform.clients empty?")
        return pa.Table.from_pylist([], schema=SCHEMA)

    async with SeiApi(settings) as api:
        fetched = await fetch_documents(
            parties=parties,
            already=_already(this),
            api=api,
            store=_Store(settings),
            core_bucket=settings.core_bucket,
            today=now.date(),
            concurrency=settings.api_concurrency,
        )
    logger.info("sei raw.client_documents: %d new document(s)", len(fetched))
    return _table(fetched, fetched_at=now)


def _wanted(ref_firms: Any) -> set[str]:
    """Firms whose clients to list. Empty when the Reference report cannot be read."""
    return set(resolve_firm_ids(ref_firms))


def _rows(handle: Any) -> list[dict[str, Any]]:
    table = previous_table(handle)
    if table is None:
        return []
    return cast(list[dict[str, Any]], table.to_pylist())


def _already(this: Any) -> set[str]:
    table = previous_table(this)
    if table is None:
        return set()
    if "document_id" not in table.column_names:
        return set()
    return {str(value) for value in table.column("document_id").to_pylist() if value}


def _table(rows: list[FetchedDocument], *, fetched_at: datetime) -> pa.Table:
    payload = [
        {
            "document_id": row.document_id,
            "firm_id": row.firm_id,
            "firm_level": row.firm_level,
            "document_type": row.document_type,
            "type_label": row.type_label,
            "feed": row.feed,
            "display_name": row.display_name,
            "party_ids": list(row.party_ids),
            "account_numbers": list(row.account_numbers),
            "bucket": row.bucket,
            "object_key": row.object_key,
            "content_type": row.content_type,
            "bytes": row.size_bytes,
            "sha256": row.sha256,
            "fetched_at": fetched_at,
        }
        for row in rows
    ]
    return pa.Table.from_pylist(payload, schema=SCHEMA)
