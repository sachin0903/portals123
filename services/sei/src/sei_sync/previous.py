"""Read a model's previous materialisation without aborting a rebuild.

After ``gc`` drops snapshots, interlace can still inject ``this`` pointing at a
name that is gone. Asking for the table is then a Binder Error.
"""

from __future__ import annotations

from typing import Any


def previous_table(this: Any) -> Any | None:
    if this is None:
        return None
    try:
        return this.table()
    except Exception:  # BinderError / Catalog Error after GC
        return None
