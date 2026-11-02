"""JSON records -> Arrow, tolerant of Dynamics OData payload shapes.

Dynamics records carry ``@odata.*`` noise (``field@odata.type`` per-property
annotations, ``@odata.type`` object tags), lookup values as ``_<name>_value``
keys, and linked-entity aliases containing ``_x002e_``. Keys are sanitised to
warehouse-safe snake_case; per-property annotations are dropped (``@odata.etag``
is kept — it is Dynamics' change signal); nested structures become JSON strings;
a column whose scalar types disagree widens to string instead of failing the
batch."""

from __future__ import annotations

import json
import re
from typing import Any

import pyarrow as pa

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def sanitise_key(key: str) -> str:
    """One Dynamics field name -> a warehouse-safe snake_case column name."""
    key = key.replace("_x002e_", "__")  # linked-entity alias separator
    key = key.replace("@", "").replace(".", "_")
    key = _CAMEL_BOUNDARY.sub("_", key).lower()
    return key.lstrip("_")  # _cam_scheme_value -> cam_scheme_value


def normalise_record(record: dict[str, Any]) -> dict[str, Any]:
    """Sanitise keys; drop OData annotations; serialise nested values."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        if "@" in key and key != "@odata.etag":  # @odata.type / field@odata.type noise
            continue
        if isinstance(value, dict | list):
            value = json.dumps(value, default=str)
        out[sanitise_key(key)] = value
    return out


def records_to_table(
    records: list[dict[str, Any]], spine: dict[str, pa.DataType] | None = None
) -> pa.Table:
    """Normalised records -> Arrow table, union of keys in first-seen order.

    ``spine`` columns are ALWAYS present (typed when the batch is empty,
    NULL-filled when no record carries them) — the model's key columns and any
    column downstream SQL references must exist even when a batch never sends
    the field (e.g. the valuation ledger's ``value`` while pushes are stubbed)."""
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


def previous_table(this: Any) -> Any | None:
    """The previous materialisation, or None if it was never built or was GC'd.

    After a warehouse rebuild, interlace still injects ``this`` pointing at a snapshot
    name the collector has dropped. Asking for the table then is a Binder Error, which
    used to abort a clean rebuild on the first ``full_merge`` model.
    """
    if this is None:
        return None
    try:
        return this.table()
    except Exception:  # BinderError / Catalog Error after GC
        return None
