"""Turn Seccl's Postman collection into one JSON Schema per resource.

Seccl publishes its API as a **Postman collection**, not an OpenAPI document: there is no
schema anywhere in the 8.2 MB file, only 377 requests carrying example request bodies and
saved response examples. So every type here is *inferred from examples* and is a starting
point, not a contract -- `services/seccl`'s hand-written models were checked against the
live API and stay authoritative where the two disagree.

Feeding those examples straight to datamodel-code-generator produces unusable output: it
infers one class per occurrence, so `/account` alone yields WrapperDetail, WrapperDetail1
and WrapperDetail2 for the same concept, and marks a field required because the single
example it saw had it. This module does the merging first -- one pass over every example
for a resource, unioning keys and types the whole way down -- so the generator sees one
schema per shape and emits one class per concept.

What the merge decides, and why:

* **Required** means "present in every example of that shape". With 31 account examples
  and no key present in all 31, that is nearly everything optional, which is the honest
  reading of a documentation corpus -- an absent key in an example is not evidence the
  field is optional, but a present-in-all-31 key is decent evidence it is not.
* **No inferred enums.** Observed value sets go in the field description, never into a
  `Literal`. The corpus is a handful of examples per field; a closed enum would reject
  valid production values, and being wrong there fails a real payment rather than a doc.
* **Dates** only when the field name says so (`*Date`, `*At`, `date`) *and* every observed
  value parses. Seccl sends both plain dates and timestamps; guessing on name alone turns
  a working string field into a validation error.
* **Numbers are floats** unless the name is count-shaped (`count`, `pageSize`, `version`...)
  and every observed value is integral. Money must not become `int` because the examples
  happened to use round numbers.
* **Postman placeholders** (`{{firmId}}`) are stand-ins, not data: they count as strings
  but are excluded from value sets and date detection, so `"{{settleDate}}"` cannot talk
  the merge out of a date.

Run via `moon run seccl:build-schemas`; the output under src/seccl/schemas/ is checked in
and reviewed like any other artefact.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parent.parent
COLLECTION = ROOT / "spec" / "seccl.postman_collection.json"
SCHEMA_DIR = ROOT / "src" / "seccl" / "schemas"

Shape = Literal["read", "create", "update"]

# The resources modelled, and why these: every one carries request bodies, which is where
# typed models earn their keep (a rejected POST costs a payment; a mistyped GET costs a
# `.get()`). Read-only resources are left to `services/seccl`, which already pulls them.
# Ordered by write-path weight -- portfoliotransactiongroup is how payments, orders,
# transfers and withdrawals are all raised, so it is the busiest shape in the API.
RESOURCES: tuple[str, ...] = (
    "portfoliotransactiongroup",
    "client",
    "account",
    "clientproduct",
    "portfoliotransaction",
    "portfoliotransactionaction",
    "portfoliogroup",
    "illustration",
    "asset",
    "model",
    "webhook",
    "event",
    "files",
)

# Class stem per resource: the collection's paths are lowercase and unspaced, which would
# give `Portfoliotransactiongroup`. Spelt out here so the generated classes read.
CLASS_NAMES: dict[str, str] = {
    "portfoliotransactiongroup": "PortfolioTransactionGroup",
    "client": "Client",
    "account": "Account",
    "clientproduct": "ClientProduct",
    "portfoliotransaction": "PortfolioTransaction",
    "portfoliotransactionaction": "PortfolioTransactionAction",
    "portfoliogroup": "PortfolioGroup",
    "illustration": "Illustration",
    "asset": "Asset",
    "model": "Model",
    "webhook": "Webhook",
    "event": "Event",
    "files": "File",
}

_PLACEHOLDER = re.compile(r"^\{\{[^}]+\}\}$")
# Base-URL variables in the collection: apiRoute (345 requests), pfolioApiRoute (27),
# authRoute (3). The resource is the segment after whichever one it is, skipping an
# explicit API version.
_URL = re.compile(r"^\{\{[^}]+\}\}/(?:v\d+/)?([A-Za-z0-9_-]+)")
_COUNTISH = re.compile(
    r"(?:^|[a-z])(?:count|version|pagesize|page|index|numberof|quantity|seq|sequence)(?:[A-Z]|$)",
    re.I,
)
_DATEISH = re.compile(r"(?:date|_at|At)$|^date", re.I)
_PLAIN_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Observed values are documented only when the set reads like a vocabulary. Beyond these
# bounds it is data, not documentation: ids, references, and free text like an account's
# name ("Fred's car savings") tell a reader nothing about the field.
# The total is bounded well under the 100-column limit because the description becomes a
# docstring in the generated module and no formatter will wrap it for us: 4 spaces of
# indent plus "observed values: " leaves ~75, and ten asset ids fit in 120 but not in that.
_MAX_DOCUMENTED_VALUES = 8
_MAX_DOCUMENTED_VALUE_LEN = 32
_MAX_DOCUMENTED_TOTAL_LEN = 70


class Samples:
    """Every example payload harvested for one resource, split by shape."""

    def __init__(self) -> None:
        self.read: list[Any] = []
        self.create: list[Any] = []
        self.update: list[Any] = []

    def of(self, shape: Shape) -> list[Any]:
        return getattr(self, shape)  # type: ignore[no-any-return]

    def __bool__(self) -> bool:
        return bool(self.read or self.create or self.update)


def iter_requests(collection: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the collection's folder tree (212 folders deep in places) to its requests."""
    found: list[dict[str, Any]] = []

    def walk(items: list[dict[str, Any]]) -> None:
        for item in items:
            if "item" in item:
                walk(item["item"])
            elif "request" in item:
                found.append(item)

    walk(collection.get("item", []))
    return found


def raw_url(request: dict[str, Any]) -> str:
    url = (request.get("request") or {}).get("url")
    if isinstance(url, dict):
        return str(url.get("raw") or "")
    return str(url or "")


def resource_of(request: dict[str, Any]) -> str | None:
    match = _URL.match(raw_url(request).strip())
    return match.group(1).lower() if match else None


def parse_body(raw: str) -> Any | None:
    """Parse an example body. Postman variables sit inside strings ("{{firmId}}") so 220
    of 222 bodies are already valid JSON; the rest are quoted-variable slips worth
    recovering rather than dropping a whole shape over."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    repaired = re.sub(r'(?<!")\{\{([^}]+)\}\}(?!")', r'"{{\1}}"', raw)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


def placeholder_paths(value: Any, prefix: tuple[Any, ...] = ()) -> set[tuple[Any, ...]]:
    """Where an example carries a Postman placeholder instead of a value.

    `{{currentdate}}` in a `paymentDate` is a hole the caller fills, not something Seccl
    sends or accepts, so it is not evidence about the type -- and validating an example
    against the model it came from has to forgive it. Forgiving by *path* rather than by
    deleting the key covers both ways a hole shows up: an unparseable value where the field
    is typed, and a missing field where the model requires it.

    Returned locations line up with pydantic's `error["loc"]`: wire names (aliases), and
    integers for list positions.
    """
    found: set[tuple[Any, ...]] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = (*prefix, key)
            if _is_placeholder(child):
                found.add(path)
            else:
                found |= placeholder_paths(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            path = (*prefix, index)
            if _is_placeholder(child):
                found.add(path)
            else:
                found |= placeholder_paths(child, path)
    return found


def unwrap(body: Any) -> list[Any]:
    """Seccl wraps every response as `{"data": ...}` (164 examples) or `{"data", "meta"}`
    (54). The envelope is transport, not the resource, so the models describe what is
    inside `data` -- a list response contributes each element."""
    payload = body.get("data") if isinstance(body, dict) and "data" in body else body
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return [payload] if isinstance(payload, dict) else []


def harvest(collection: dict[str, Any]) -> dict[str, Samples]:
    """Collect every example for every resource, keyed by shape.

    PATCH/PUT bodies are a shape of their own rather than more evidence about the resource:
    Seccl updates are partial documents carrying an `updateId` for optimistic concurrency,
    so merging them into the read shape would make half the resource look optional and
    invent an `updateId` on reads.
    """
    out: dict[str, Samples] = {name: Samples() for name in RESOURCES}
    for request in iter_requests(collection):
        resource = resource_of(request)
        if resource not in out:
            continue
        samples = out[resource]
        spec = request.get("request") or {}
        method = str(spec.get("method") or "").upper()
        body_raw = (spec.get("body") or {}).get("raw")
        if body_raw:
            body = parse_body(body_raw)
            if isinstance(body, dict):
                if method == "POST":
                    samples.create.append(body)
                elif method in {"PATCH", "PUT"}:
                    samples.update.append(body)
        for response in request.get("response") or []:
            code = str(response.get("code") or "")
            if not code.startswith("2") or not response.get("body"):
                continue
            parsed = parse_body(str(response["body"]))
            if parsed is not None:
                samples.read.extend(unwrap(parsed))
    return out


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and bool(_PLACEHOLDER.match(value))


def _string_schema(name: str, values: list[str]) -> dict[str, Any]:
    real = [v for v in values if not _PLACEHOLDER.match(v)]
    node: dict[str, Any] = {"type": "string"}
    if real and _DATEISH.search(name):
        if all(_is_plain_date(v) for v in real):
            node["format"] = "date"
            return node
        if all(_is_aware_datetime(v) for v in real):
            node["format"] = "date-time"
            return node
        # Parses as a timestamp but not consistently: Seccl sends `declarationAcceptedDate`
        # both with and without an offset, and `format: date-time` becomes pydantic's
        # `AwareDatetime`, which rejects the naive half. A string keeps every real payload
        # parseable -- the caller converts, knowing which endpoint it came from.
    distinct = sorted({v for v in real if v})
    joined = ", ".join(distinct)
    if (
        distinct
        and len(distinct) <= _MAX_DOCUMENTED_VALUES
        and max(len(v) for v in distinct) <= _MAX_DOCUMENTED_VALUE_LEN
        and len(joined) <= _MAX_DOCUMENTED_TOTAL_LEN
    ):
        # Documentation, deliberately not `enum` -- see the module docstring.
        node["description"] = "observed values: " + joined
    return node


def _is_plain_date(value: str) -> bool:
    """A calendar date and nothing else. The regex is load-bearing: since 3.11
    `date.fromisoformat("2026-01-01T09:30:00Z")` succeeds and quietly drops the time, which
    typed `paymentDate` as a date and then rejected every timestamp Seccl actually sends."""
    if not _PLAIN_DATE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_aware_datetime(value: str) -> bool:
    """A timestamp carrying an offset. Without one, `format: date-time` generates
    `AwareDatetime` and fails on the value it was inferred from."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def infer(values: list[Any], *, name: str = "", force_optional: bool = False) -> dict[str, Any]:
    """Merge every observation of one field (or of a whole shape) into one schema node."""
    kinds = Counter(_kind(v) for v in values)
    nullable = "null" in kinds
    present = [v for v in values if v is not None]
    if not present:
        # Only ever seen as null: say so rather than inventing a type.
        return {"type": "null"}

    concrete = {k for k in kinds if k != "null"}
    if concrete == {"integer", "number"}:
        concrete = {"number"}

    if len(concrete) > 1:
        # A field Seccl sends as more than one JSON type (a scalar on one endpoint, an
        # object on another -- `node_id` does this). anyOf keeps both readable.
        node: dict[str, Any] = {
            "anyOf": [
                infer(
                    [
                        v
                        for v in present
                        if _kind(v) == kind or (kind == "number" and _kind(v) == "integer")
                    ],
                    name=name,
                )
                for kind in sorted(concrete)
            ]
        }
        return _nullable(node, nullable)

    kind = next(iter(concrete))
    if kind == "object":
        objects = [v for v in present if isinstance(v, dict)]
        keys: dict[str, list[Any]] = {}
        counts: Counter[str] = Counter()
        for obj in objects:
            for key, value in obj.items():
                keys.setdefault(key, []).append(value)
                counts[key] += 1
        properties = {
            key: infer(vals, name=key, force_optional=force_optional) for key, vals in keys.items()
        }
        node = {"type": "object", "properties": properties}
        required = sorted(k for k, n in counts.items() if n == len(objects))
        if force_optional:
            # Updates are partial by definition; `updateId` is the one field Seccl
            # requires on every one of them.
            required = [k for k in required if k == "updateId"]
        if required:
            node["required"] = required
        return _nullable(node, nullable)

    if kind == "array":
        items: list[Any] = []
        for value in present:
            if isinstance(value, list):
                items.extend(value)
        node = {"type": "array", "items": infer(items, name=name) if items else {}}
        return _nullable(node, nullable)

    if kind == "string":
        return _nullable(_string_schema(name, [v for v in present if isinstance(v, str)]), nullable)

    if kind == "integer" and not _COUNTISH.search(name):
        # Integral in the examples, but nothing says it stays integral -- a money field
        # that happened to be round must not become `int`.
        return _nullable({"type": "number"}, nullable)

    return _nullable({"type": kind}, nullable)


def _nullable(node: dict[str, Any], nullable: bool) -> dict[str, Any]:
    if not nullable:
        return node
    if "anyOf" in node:
        node["anyOf"].append({"type": "null"})
        return node
    inner = node.pop("type", None)
    if inner is None:
        return {"anyOf": [node, {"type": "null"}]}
    node["type"] = [inner, "null"]
    return node


def _pascal(name: str) -> str:
    parts = re.split(r"[_\-\s]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _singular(name: str) -> str:
    if name.endswith("ies") and len(name) > 4:
        return name[:-3] + "y"
    if name.endswith("ses") or name.endswith("xes"):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def collect_concepts(samples: Samples) -> dict[str, list[Any]]:
    """Pool every observation of each nested object in a resource, keyed by concept name.

    A resource's three shapes each carry their own copy of the same nested objects -- an
    account's `wrapperDetail` appears in the read shape, the create body and the update
    body -- and inferring each in isolation is what produced WrapperDetail, WrapperDetail1
    and WrapperDetail2. Pooling first means one definition per concept, built from every
    example of it anywhere in the resource.

    Pooling also widens what counts as optional: a create body carries fewer keys than a
    read, so a field required in reads alone stops being required here. That direction is
    the safe one -- over-requiring rejects payloads Seccl accepts.
    """
    pool: dict[str, list[Any]] = {}

    def visit(value: Any, concept: str | None) -> None:
        if isinstance(value, dict):
            if concept:
                pool.setdefault(concept, []).append(value)
            for key, child in value.items():
                if isinstance(child, dict):
                    visit(child, _pascal(key))
                elif isinstance(child, list):
                    for item in child:
                        if isinstance(item, dict):
                            visit(item, _pascal(_singular(key)))
                        elif isinstance(item, list):
                            visit(item, None)
        elif isinstance(value, list):
            for item in value:
                visit(item, concept)

    for shape in ("read", "create", "update"):
        for sample in samples.of(shape):
            visit(sample, None)
    return pool


def _types_of(node: dict[str, Any]) -> list[Any]:
    """A node's JSON types as a list: `type` is a string, or a list once it is nullable."""
    kind = node.get("type")
    return kind if isinstance(kind, list) else [kind]


def _object_ish(node: dict[str, Any]) -> bool:
    return "object" in _types_of(node) and bool(node.get("properties"))


def refify(
    node: dict[str, Any], defs: dict[str, Any], *, concept: str | None = None
) -> dict[str, Any]:
    """Point every nested object at its pooled definition instead of restating it."""
    if concept and _object_ish(node) and concept in defs:
        ref = {"$ref": f"#/$defs/{concept}"}
        return {"anyOf": [ref, {"type": "null"}]} if "null" in _types_of(node) else ref
    if "anyOf" in node:
        return {**node, "anyOf": [refify(b, defs, concept=concept) for b in node["anyOf"]]}
    if _object_ish(node):
        return {
            **node,
            "properties": {
                key: refify(child, defs, concept=_pascal(key))
                for key, child in node["properties"].items()
            },
        }
    if "array" in _types_of(node) and isinstance(node.get("items"), dict) and node["items"]:
        item_concept = _pascal(_singular(concept)) if concept else None
        return {**node, "items": refify(node["items"], defs, concept=item_concept)}
    return node


_SHAPE_BLURB = {
    "read": "{stem} as Seccl returns it",
    "create": "Body for creating a {stem}",
    "update": (
        "Partial update to a {stem}; where the endpoint takes an `updateId` it is Seccl's"
        " optimistic-concurrency token"
    ),
}


def schema_for(resource: str, samples: Samples) -> dict[str, Any]:
    """One schema document per resource: the read shape as the root, the write shapes and
    every pooled nested concept under `$defs`.

    The root is a real shape rather than an empty container because datamodel-code-generator
    models the document root: given only `$defs` it emits `RootModel[Any]` and then names
    the actual shapes `Account1`, `Account2`.
    """
    stem = CLASS_NAMES[resource]
    titles = {"read": stem, "create": f"{stem}Create", "update": f"{stem}Update"}

    concepts = collect_concepts(samples)
    # A concept is worth hoisting once it is a shared or repeated shape; a one-off nested
    # object reads better inline, next to the field that holds it.
    defs: dict[str, Any] = {
        name: infer(values, name=name)
        for name, values in sorted(concepts.items())
        if len(values) > 1 and name not in titles.values()
    }
    # The definitions nest too -- a pooled `Client` carries its own `bankDetails` -- so they
    # need the same treatment as the shapes, or the generator restates those inner objects
    # once per definition (BankDetails, BankDetails1, ...). A definition that legitimately
    # contains itself becomes a recursive `$ref`, which is what pydantic's forward
    # references are for.
    defs = {name: refify(node, defs) for name, node in defs.items()}

    shapes: dict[str, dict[str, Any]] = {}
    for shape in ("read", "create", "update"):
        values = samples.of(shape)
        if not values:
            continue
        node = refify(infer(values, force_optional=shape == "update"), defs)
        node["title"] = titles[shape]
        node["description"] = _SHAPE_BLURB[shape].format(stem=stem) + (
            f" -- inferred from {len(values)} example(s) in the Postman collection."
        )
        shapes[titles[shape]] = node

    root_title = next(titles[s] for s in ("read", "create", "update") if titles[s] in shapes)
    root = shapes.pop(root_title)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **root,
        "$defs": {**shapes, **defs},
    }


def build() -> dict[str, dict[str, Any]]:
    collection = json.loads(COLLECTION.read_text())
    harvested = harvest(collection)
    return {
        resource: schema_for(resource, samples)
        for resource, samples in harvested.items()
        if samples
    }


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    schemas = build()
    for resource, schema in schemas.items():
        path = SCHEMA_DIR / f"{resource}.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=False) + "\n")
        shapes = ", ".join(schema["$defs"])
        print(f"{path.relative_to(ROOT)}: {shapes}")
    missing = [r for r in RESOURCES if r not in schemas]
    if missing:
        print(f"no examples found for: {', '.join(missing)}")


if __name__ == "__main__":
    main()
