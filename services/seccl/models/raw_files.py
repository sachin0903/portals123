"""Client documents referenced by messages.

The bytes go to the core bucket's raw zone; this model lands only the metadata,
keyed by Seccl's file id. It anti-joins its own previous materialisation (the
reserved ``this`` parameter) so each run fetches only documents it has never
seen — a message's document is immutable, so "present" means "done".
"""

from __future__ import annotations

import logging
from typing import Any

import pyarrow as pa
from interlace import model

from seccl_sync.client import SecclClient
from seccl_sync.files import ObjectStore, fetch_all, pending
from seccl_sync.records import records_to_table

logger = logging.getLogger("seccl_sync.models")

_SPINE: dict[str, pa.DataType] = {
    "id": pa.string(),
    "message_id": pa.string(),
    "object_key": pa.string(),
    "sha256": pa.string(),
}


@model(
    name="raw.files",
    strategy="merge",
    key="id",
    depends_on="raw.messages",
    schedule={"cron": "30 3 * * *"},
    tags=("seccl", "raw"),
    description="Client documents behind messages; bytes to object storage, metadata here",
)
async def raw_files(this: Any, raw_messages: Any) -> pa.Table:
    messages = raw_messages.table()
    columns = {name: messages.column(name).to_pylist() for name in messages.column_names}

    already_have: set[str] = set()
    if this is not None:
        mine = this.table()
        if "id" in mine.column_names:
            already_have = {str(value) for value in mine.column("id").to_pylist() if value}

    items = pending(columns, already_have)
    if not items:
        return records_to_table([], spine=_SPINE)

    store = ObjectStore()
    async with SecclClient() as api:
        cap = api.settings.files_max_per_run
        if cap and len(items) > cap:
            logger.warning("raw.files: %d documents pending, fetching %d this run", len(items), cap)
            items = items[:cap]
        records = await fetch_all(items, api=api, store=store)
    return records_to_table(records, spine=_SPINE)
