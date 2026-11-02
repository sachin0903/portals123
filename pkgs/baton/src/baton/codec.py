"""JSON normalisation. A value is stored through this, and the body sees the stored shape.

Pass 1 of a replay runs the body against fresh Python objects; pass N runs it against what came
back from Postgres. Normalising on the way IN (tuples to lists, datetimes to ISO strings,
Decimals to strings, models to dicts) makes both passes see the same thing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

type Redactor = Callable[[Any], Any]


def _default(value: Any) -> Any:
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set | frozenset):
        return sorted(value, key=repr)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    raise TypeError(f"{type(value).__name__} is not JSON-serialisable")


#: The JSON ``default`` every store write goes through, so a Decimal, a date or a UUID inside an
#: attempt body is text in the row rather than a TypeError that leaves the run leased 'running'.
json_default = _default


class JsonCodec:
    """``normalise`` is a JSON round-trip; ``dumps`` is the canonical text; ``digest`` hashes it."""

    def dumps(self, value: Any) -> str:
        return json.dumps(value, default=_default, sort_keys=True, separators=(",", ":"))

    def normalise(self, value: Any) -> Any:
        if value is None:
            return None
        return json.loads(self.dumps(value))

    def params(self, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        out = self.normalise(dict(kwargs))
        return dict(out) if isinstance(out, dict) else {}

    def digest(self, value: Any) -> str:
        return hashlib.sha256(self.dumps(value).encode("utf-8")).hexdigest()[:32]


def identity(value: Any) -> Any:
    return value
