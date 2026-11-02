"""Typed access to SEI's documented services (`vendor_manifest.json`).

The JSON is a machine transcription of the API sheet SEI issue per firm, produced by
`scripts/build_manifest.py`. This is the read side, and the one thing it must keep saying out
loud is HOW WEAK the source is compared with Morgan Lloyd's:

  * the sheet has no Required column, so nothing here is required. A form cannot pre-empt a
    vendor rejection the way it can for ML, and pretending otherwise would either block
    legitimate partial calls or present a guess as knowledge;
  * types come from the sample VALUE, so an integer sample of a field that accepts decimals
    reads as `int`;
  * one example is one shape -- a field the sample omits is absent here.

`services` are keyed by their PATH (`swpaccountstatuschangeservice/accountStatusChange`), because
the vendor's display names are prose and change between issues of the sheet while the path is the
thing the client actually calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

__all__ = ["Field", "Service", "load", "service", "services"]


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    """A DOTTED PATH into the request body (`data.attributes.accountStatus`), not a flat key.

    SEI wraps every request in a JSON:API-ish envelope, so the leaf is what a form collects and
    the envelope is what the caller rebuilds. Keeping the path whole means the rebuild is
    mechanical rather than a per-service mapping table.
    """
    type: str
    required: bool = False
    description: str = ""

    @property
    def leaf(self) -> str:
        """The last segment -- what a form labels the input."""
        return self.name.rsplit(".", 1)[-1]


@dataclass(frozen=True, slots=True)
class Service:
    name: str
    method: str
    path: str
    category: str
    product: str
    fields: tuple[Field, ...] = ()
    repeating: tuple[str, ...] = ()
    """Arrays in the body, by path. A flat form cannot build one, so a caller either omits the
    service or states which part it is not offering."""
    depth: int = 0


@cache
def load() -> dict[str, Any]:
    text = resources.files("sei").joinpath("vendor_manifest.json").read_text("utf-8")
    return dict(json.loads(text))


@cache
def services() -> tuple[Service, ...]:
    out: list[Service] = []
    for raw in load().get("services", []):
        out.append(
            Service(
                name=str(raw.get("name", "")),
                method=str(raw.get("method", "")),
                path=str(raw.get("path", "")),
                category=str(raw.get("category", "")),
                product=str(raw.get("product", "")),
                fields=tuple(
                    Field(name=str(f["name"]), type=str(f.get("type", "string")))
                    for f in raw.get("fields") or []
                ),
                repeating=tuple(raw.get("repeating") or []),
                depth=int(raw.get("depth") or 0),
            )
        )
    return tuple(out)


def service(path: str) -> Service | None:
    return next((s for s in services() if s.path == path), None)
