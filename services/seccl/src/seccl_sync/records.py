"""JSON records -> Arrow, tolerant of the drift Seccl's payloads exhibit.

Keys are snake_cased; nested structures become JSON strings (their internal
shape is inconsistent across records — the v1 pipeline's biggest source of
load failures); a column whose scalar types disagree widens to string instead
of failing the batch.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import pyarrow as pa

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def snake_case(name: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def normalise_record(record: dict[str, Any]) -> dict[str, Any]:
    """snake_case the keys; serialise nested dicts/lists to JSON strings."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, dict | list):
            value = json.dumps(value, default=str)
        out[snake_case(key)] = value
    return out


def records_to_table(
    records: list[dict[str, Any]], spine: dict[str, pa.DataType] | None = None
) -> pa.Table:
    """Normalised records -> Arrow table, union of keys in first-seen order.

    ``spine`` columns are ALWAYS present (typed when the batch is empty,
    NULL-filled when no record carries them) — the model's key/cursor columns
    and any column downstream SQL references must exist even in environments
    where the API never sends the field (e.g. ``charges`` on staging).
    """
    if not records:
        spine = spine or {"id": pa.string()}
        return pa.table({name: pa.array([], type=dtype) for name, dtype in spine.items()})
    rows = [normalise_record(record) for record in records]
    columns: dict[str, None] = {}
    for key in spine or {}:
        columns.setdefault(key)
    for row in rows:
        for key in row:
            columns.setdefault(key)
    arrays: dict[str, pa.Array] = {}
    for name in columns:
        values = [row.get(name) for row in rows]
        # Spine columns keep their declared type — an all-NULL column would
        # otherwise infer Arrow's null type and be dropped by the warehouse.
        dtype = (spine or {}).get(name)
        try:
            arrays[name] = pa.array(values, type=dtype)
        except (pa.ArrowInvalid, pa.ArrowTypeError):  # mixed types: widen to string
            arrays[name] = pa.array(
                [
                    value
                    if isinstance(value, str)
                    else None
                    if value is None
                    else json.dumps(value, default=str)
                    for value in values
                ],
                type=pa.string(),
            )
    return pa.table(arrays)


def event_key(kind: str, event: dict[str, Any]) -> str:
    """Stable idempotency key for one webhook event (kind + identity + updateDate)."""
    identity = event.get("id") or event.get("transactionId") or event.get("clientId") or ""
    stamp = event.get("updateDate") or event.get("creationDate") or ""
    if not identity and not stamp:  # fall back to the whole payload
        seed = json.dumps(event, sort_keys=True, default=str)
    else:
        seed = f"{identity}:{stamp}"
    return hashlib.sha256(f"{kind}:{seed}".encode()).hexdigest()[:24]
