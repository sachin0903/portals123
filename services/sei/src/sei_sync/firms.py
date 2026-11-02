"""The firm list the API models request.

It is the ``FIRM_ID`` dimension of the SFTP Reference report (``ref.firms``). A
hand-maintained list is how a firm already in the drop — Chester Rose, then
Vertex — never had its transactions, templates or staff requested.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("sei_sync.models")


def reference_firm_ids(handle: Any) -> set[str] | None:
    """Firm ids on ``ref.firms``, or None when that table cannot be read.

    A readable table with no rows is an empty set. That is a drop that named
    nobody, and it must not fall through to every account in the file.
    """
    if handle is None:
        return None
    try:
        table = handle.table()
    except Exception:  # noqa: BLE001 - an unreadable upstream requests nobody
        logger.warning("sei firms: ref.firms could not be read")
        return None
    if table is None:
        return None
    columns = {name.upper(): name for name in table.column_names}
    column = columns.get("FIRM_ID")
    if column is None:
        logger.warning("sei firms: ref.firms has no firm_id (%s)", table.column_names[:8])
        return None
    return {
        str(value).strip()
        for value in table.column(column).to_pylist()
        if value is not None and str(value).strip()
    }


def resolve_firm_ids(handle: Any) -> list[str]:
    """Firms to request, in a stable order. Empty when the report cannot be read."""
    found = reference_firm_ids(handle)
    if not found:
        return []
    return sorted(found)
