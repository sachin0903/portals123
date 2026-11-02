"""Scrape the Morgan Lloyd docs site into the semantic half of the catalogue.

`introspect_api.py` gets names and types from `$metadata`, which is authoritative on both and
silent on everything else: CSDL marks *every* action parameter `Nullable="true"` regardless of
business rule, option-set parameters are bare `Int32` with no labels, and there are no
descriptions or examples. All of that lives only in the human docs at
https://dev.api.morgan-lloyd.co.uk/ — a Slate single-pager whose markup is regular enough to
read mechanically:

    <h1>              group ("Financial Advisers")
    <h2>              operation ("Create Financial Adviser Contact") + summary <p>
      <pre .http>     request example (headers + JSON body)
      <pre .json>     response example
      <h3>HTTP Request<p><code>POST https://…/cam_CreateFinancialAdviserContact</code>
      <h3>Fields          table: Field | Type | Required | Description
      <h3>Response Fields table: Field | Type | Description
      <h3>…               any other table (nested-JSON shapes, lookup tables)

    uv run python scripts/enrich_catalogue.py                  # fetch live
    uv run python scripts/enrich_catalogue.py --html page.html # or read a saved copy

Output: `docs-catalogue.json` beside the package, plus a drift report against
`catalogue.json` on stdout. The two files stay SEPARATE on purpose — either source can be
re-fetched without clobbering the other, and they disagree often enough that collapsing them
would hide the disagreements. `build_openapi.py` is what merges them, with CSDL winning on
names and types.

Note the operation *titles* are not the action names and cannot be derived from them: the
section titled "Create Investment" posts to `cam_createwrapper`, and there is no
`cam_createinvestment` in the org. The action is read from the HTTP Request block, never
guessed from the heading.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = PACKAGE_ROOT / "catalogue.json"
DOCS_CATALOGUE = PACKAGE_ROOT / "docs-catalogue.json"
DOCS_URL = "https://dev.api.morgan-lloyd.co.uk/"

# Slate writes single-quoted ids and never nests these headings, so a flat scan over heading
# positions reconstructs the tree without parsing a DOM.
_HEADING = re.compile(
    r"<h(?P<level>[123]) id='(?P<anchor>[^']*)'>(?P<title>.*?)</h(?P=level)>", re.S
)
_HTTP_LINE = re.compile(r"<p><code>(?P<method>[A-Z]+)\s+(?P<url>[^<]+)</code></p>", re.S)
_TABLE = re.compile(r"<table>(?P<body>.*?)</table>", re.S)
_ROW = re.compile(r"<tr>(?P<cells>.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(?P<text>.*?)</t[dh]>", re.S)
_CODE_BLOCK = re.compile(
    r'<pre class="highlight (?P<kind>[^"]*)"><code>(?P<body>.*?)</code></pre>', re.S
)
_TAG = re.compile(r"<[^>]+>")
# "Allowed Values (<br><strong>Mr</strong>: 809130000, …)" — the colon sits inside the <strong>
# about half the time ("<strong>Male:</strong> 1"), so accept it on either side.
_ENUM_MEMBER = re.compile(r"<strong>\s*(?P<label>[^<]+?)\s*:?\s*</strong>\s*:?\s*(?P<value>-?\d+)")
# Where the allowed-value list starts. "Values" is sometimes doubled ("Possible Values: Values
# (…)") and sometimes reads "values are:", so consume the slack rather than require one spelling.
_ENUM_ANCHOR = re.compile(
    r"(?:Allowed|Possible)\s+[Vv]alues?\b(?:\s*:?\s*[Vv]alues?\b)?\s*(?:are)?\s*:?\s*", re.I
)
_INTEGER = re.compile(r"-?\d+")
# Fallback for the string-valued option sets ("cam_personalpolicy", "systemuser", "team").
_STRING_MEMBER = re.compile(r'"([^"]+)"|\b([a-z][a-z0-9_]{2,})\b')
_PROSE = frozenset({"and", "are", "the", "matching", "returned", "response", "either", "one"})
# Where an operation's own prose stops and Slate's example furniture starts.
_PREAMBLE_END = re.compile(r"<blockquote>|<div class=\"highlight\"")
# The docs' own heading for the request payload varies; both of these mean the body.
_REQUEST_TABLES = {"fields", "body data"}
# Kept apart from the body: a path GUID or an OData $filter is not a payload key, and merging
# them made the path GUID look like a body field the action was missing.
_URL_TABLES = {"url parameters", "url query parameters"}
_RESPONSE_TABLES = {"response fields", "withdrawal response fields"}
# Change Log lists releases as h2s, and Authentication documents the Azure AD token endpoint on
# a different host entirely — neither is a Dataverse operation, so skip the whole group.
_SKIP_GROUPS = {"Change Log", "Introduction", "Errors", "Authentication", "Api Response"}


def fetch_html(url: str = DOCS_URL) -> str:
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https URL
        return response.read().decode("utf-8", errors="replace")


def _text(fragment: str) -> str:
    """Strip markup to plain text, turning each tag into a space so <br>-separated enum
    labels do not fuse into one another."""
    return html.unescape(_TAG.sub(" ", fragment)).replace("\xa0", " ").strip()


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _parse_table(body: str) -> list[dict[str, str]]:
    """A Slate table as a list of {header: cell} dicts, headers lower-cased."""
    rows = [[cell["text"] for cell in _CELL.finditer(row["cells"])] for row in _ROW.finditer(body)]
    if not rows:
        return []
    headers = [_collapse(_text(cell)).lower() for cell in rows[0]]
    return [
        dict(zip(headers, cells, strict=False))
        for cells in rows[1:]
        if any(_text(cell) for cell in cells)
    ]


def _required(value: str | None) -> bool | None:
    """The docs say Yes/No, sometimes qualified ("Yes - if regular"). None when not stated."""
    if value is None:
        return None
    text = _collapse(_text(value)).lower()
    if text.startswith("yes"):
        return True
    if text.startswith("no"):
        return False
    return None


def _field(row: dict[str, str]) -> dict[str, Any]:
    raw_description = row.get("description", "")
    # "Field" in body tables, "Parameter" in URL-parameter tables, "Key" in $-query tables.
    raw_name = _collapse(_text(row.get("field") or row.get("parameter") or row.get("key") or ""))
    # The docs suffix some field names with markers explained in a footnote under the table —
    # "Fields marked with ** will not update if 0 or null is provided". Strip them so the name
    # is usable as a JSON key and matches CSDL, but keep the marker: it is real behaviour.
    name = raw_name.rstrip("*")
    field: dict[str, Any] = {
        "name": name,
        "type": _collapse(_text(row.get("type", ""))) or None,
        "description": _collapse(_text(raw_description)) or None,
    }
    if name != raw_name:
        field["name_marker"] = raw_name[len(name) :]
    if "required" in row:
        field["required"] = _required(row["required"])
        # "Yes - only when X" carries the condition; the flag alone loses it.
        conditional = _collapse(_text(row["required"]))
        if conditional.lower() not in {"yes", "no", ""}:
            field["required_note"] = conditional
    field.update(_options(raw_description))
    return field


def _enum_clause(description: str) -> str | None:
    """The allowed-value list out of a field description, or None.

    Scoped tightly because descriptions carry other numbers around it ("…809130017) For more
    information on what SIPP Type to use…"). Parentheses are matched by counting rather than by
    `[^)]*`: labels contain their own — "Own (Mortgaged): 809130000".
    """
    anchor = _ENUM_ANCHOR.search(description)
    if not anchor:
        return None
    rest = description[anchor.end() :]
    if not rest.startswith("("):
        # No parenthetical — the string-valued sets just list values to the end of the sentence.
        return rest.split(".")[0] or None
    depth = 0
    for index, character in enumerate(rest):
        depth += (character == "(") - (character == ")")
        if depth == 0:
            return rest[1:index]
    return rest[1:]


def _options(description: str) -> dict[str, Any]:
    clause = _enum_clause(description)
    if not clause:
        return {}
    found: dict[str, Any] = {}
    members = {
        _collapse(html.unescape(m["label"])): int(m["value"]) for m in _ENUM_MEMBER.finditer(clause)
    }
    if members:
        found["enum"] = members
    values: list[Any] = [int(v) for v in _INTEGER.findall(clause)]
    if not values:
        # A handful of option sets are string-valued: entityType takes "cam_scheme", and a
        # Task's ownerEntityType takes systemuser or team. Where the docs quote them, trust the
        # quotes exactly — the unquoted scan would drag in the trailing prose of the sentence.
        text = html.unescape(_TAG.sub(" ", clause))
        matches = _STRING_MEMBER.findall(text)
        quoted = [q for q, _ in matches if q]
        values = quoted or [bare for _, bare in matches if bare not in _PROSE]
    # Some allowed values carry no label at all — `sippType` lists fourteen bare integers, and
    # `schemeType` labels 16 as "ML SIPP" but leaves 17 anonymous. Keeping only the labelled
    # ones silently narrows what the API accepts, so record the full set whenever it differs.
    if values and sorted(set(values), key=str) != sorted(set(members.values()), key=str):
        found["enum_values"] = sorted(set(values), key=str)
    return found


def _examples(fragment: str) -> dict[str, str]:
    """The request example (an HTTP block) and the response example (a JSON block)."""
    found: dict[str, str] = {}
    for match in _CODE_BLOCK.finditer(fragment):
        kind = match["kind"]
        body = html.unescape(_TAG.sub("", match["body"]))
        if "tab-http" in kind and "request" not in found:
            found["request"] = body.strip()
        elif "tab-json" in kind and "response" not in found:
            found["response"] = body.strip()
    return found


def _target(method: str, url: str) -> tuple[str | None, str | None]:
    """(action, entity_set) for a documented URL.

    Only an unbound action call is an action: `POST …/v9.1/cam_Foo`. The docs also document
    plain OData CRUD against entity *sets* — `GET …/cam_nominationofbeneficiaries`,
    `PATCH …/cam_contributions(GUID)` — which share the `cam_` prefix but are tables, not
    operations. Conflating the two makes seven entity sets look like actions that 404.
    """
    segment = html.unescape(url).split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    if not segment.startswith("cam_"):
        return None, None
    if method != "POST" or "(" in segment:
        return None, segment.split("(")[0]
    return segment, None


def docs_version(page: str) -> str | None:
    """The newest release in the Change Log — the only version number the docs state, and the
    right thing to stamp a generated spec with."""
    match = re.search(r"<h2 id='release-[^']*'>Release\s+([\d.]+)</h2>", page)
    return match.group(1) if match else None


def parse_docs(page: str) -> list[dict[str, Any]]:
    content = page[page.find('<div class="content">') :]
    headings = [
        (int(m["level"]), m["anchor"], _collapse(html.unescape(_TAG.sub("", m["title"]))), m.end())
        for m in _HEADING.finditer(content)
    ]
    # Each heading owns the text up to the next heading of any level.
    bounds = [
        (level, anchor, title, start, headings[i + 1][3] if i + 1 < len(headings) else len(content))
        for i, (level, anchor, title, start) in enumerate(headings)
    ]

    operations: list[dict[str, Any]] = []
    group = ""
    current: dict[str, Any] | None = None
    for level, anchor, title, start, end in bounds:
        fragment = content[start:end]
        if level == 1:
            group = title
            current = None
            continue
        if group in _SKIP_GROUPS:
            continue
        if level == 2:
            current = {
                "group": group,
                "title": title,
                "anchor": anchor,
                "summary": None,
                "method": None,
                "url": None,
                "action": None,
                "entity_set": None,
                "fields": [],
                "url_parameters": [],
                "headers": [],
                "response_fields": [],
                "tables": {},
                "notes": [],
                "examples": _examples(fragment),
            }
            # The summary is the prose immediately under the heading. Anything from the first
            # code block or blockquote onward is Slate's example-pane furniture — take the
            # boilerplate "The above command returns HTTP structured like this:" as a summary
            # and every undescribed operation ends up with the same meaningless one.
            preamble = _PREAMBLE_END.split(fragment, maxsplit=1)[0]
            summary = re.search(r"<p>(.*?)</p>", preamble, re.S)
            if summary:
                current["summary"] = _collapse(_text(summary.group(1)))
            # A few operations put the whole thing under the h2 with no h3s at all.
            _absorb(current, title, fragment)
            operations.append(current)
            continue
        if current is not None:
            _absorb(current, title, fragment)
    return operations


def _absorb(operation: dict[str, Any], heading: str, fragment: str) -> None:
    """Fold one h3 (or the h2's own body) into the operation being built."""
    http = _HTTP_LINE.search(fragment)
    if http and not operation["method"]:
        operation["method"] = http["method"]
        operation["url"] = _collapse(html.unescape(http["url"]))
        operation["action"], operation["entity_set"] = _target(http["method"], http["url"])
    key = heading.lower().strip()
    for table in _TABLE.finditer(fragment):
        rows = _parse_table(table["body"])
        if not rows:
            continue
        fields = [field for field in map(_field, rows) if field["name"]]
        if key in _REQUEST_TABLES:
            operation["fields"].extend(fields)
            # The footnotes under a Fields table explain its markers and conditional
            # requirements ("*Required if creating a Joint Liability"). Losing them loses the
            # only statement of when a nominally-optional field is actually mandatory.
            operation["notes"].extend(
                note
                for note in (
                    _collapse(_text(p)) for p in re.findall(r"<p>(.*?)</p>", fragment, re.S)
                )
                if note
            )
        elif key in _URL_TABLES:
            operation["url_parameters"].extend(fields)
        elif key == "headers":
            operation["headers"].extend(fields)
        elif key in _RESPONSE_TABLES:
            operation["response_fields"].extend(fields)
        else:
            # Nested-payload shapes ("Fund Splits JSON") and lookup tables ("Relationship
            # Types") — kept verbatim rather than flattened into fields they don't belong to.
            operation["tables"].setdefault(heading, []).extend(rows)


def drift_report(operations: list[dict[str, Any]], catalogue: dict[str, dict[str, Any]]) -> None:
    documented = {op["action"] for op in operations if op["action"]}
    entity_sets = {op["entity_set"] for op in operations if op["entity_set"]}
    print(f"documented operations : {len(operations)} ({len(documented)} distinct cam_* actions)")
    print(f"entity sets (OData CRUD, not actions): {', '.join(sorted(entity_sets))}")
    print(f"actions in $metadata  : {len(catalogue)}")

    ghosts = sorted(documented - set(catalogue))
    if ghosts:
        # A POST target absent from the action list is a record create against an entity set,
        # not a missing action — worth naming, but it is not broken.
        print(f"\nDOCUMENTED POST TARGETS THAT ARE NOT ACTIONS ({len(ghosts)}):")
        for action in ghosts:
            print(f"   {action}  (an entity-set create, or a doc error)")

    undocumented = sorted(set(catalogue) - documented)
    print(f"\nIN THE ORG, UNDOCUMENTED ({len(undocumented)}):")
    print("   " + ", ".join(undocumented))

    print("\nFIELD DRIFT (docs vs $metadata, per action):")
    clean = 0
    for operation in operations:
        action = operation["action"]
        if not action or action not in catalogue:
            continue
        # `entity` is the bound-action receiver, supplied by the URL, never in a payload.
        csdl = {p["name"] for p in catalogue[action]["params"] if p["name"] != "entity"}
        docs = {f["name"] for f in operation["fields"] if f["name"]}
        extra = docs - csdl
        missing = csdl - docs
        # OData parameter names are case-sensitive, so a docs/CSDL case difference is a call
        # that silently drops the parameter. Called out on its own: it is the actionable kind.
        folded = {name.lower(): name for name in csdl}
        miscased = {name: folded[name.lower()] for name in sorted(extra) if name.lower() in folded}
        extra -= set(miscased)
        missing -= set(miscased.values())
        if not extra and not missing and not miscased:
            clean += 1
            continue
        print(f"   {action}  ({operation['title']})")
        for wrong, right in miscased.items():
            print(f"      MISCASED  : docs say {wrong!r}, the org wants {right!r}")
        if extra:
            # Documented but absent from CSDL: a doc typo, or a payload key nested inside a
            # String parameter — the docs flatten JSON-in-a-string params into their members.
            print(f"      docs-only : {', '.join(sorted(extra))}")
        if missing:
            print(f"      CSDL-only : {', '.join(sorted(missing))}")
    print(f"   ({clean} actions agree exactly)")

    required = sum(1 for op in operations for f in op["fields"] if f.get("required"))
    enums = sum(1 for op in operations for f in op["fields"] if f.get("enum"))
    print(f"\nenriched: {required} required fields, {enums} fields carrying option-set labels")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape the Morgan Lloyd docs site.")
    parser.add_argument("--html", type=Path, help="read a saved copy instead of fetching")
    parser.add_argument("--url", default=DOCS_URL)
    parser.add_argument("--out", type=Path, default=DOCS_CATALOGUE)
    args = parser.parse_args(argv)

    page = args.html.read_text(errors="replace") if args.html else fetch_html(args.url)
    operations = parse_docs(page)
    if not operations:
        print("no operations parsed — has the docs site stopped being Slate-generated?")
        return 1

    document = {"source": args.url, "version": docs_version(page), "operations": operations}
    args.out.write_text(json.dumps(document, indent=2) + "\n")
    inside = args.out.is_relative_to(PACKAGE_ROOT)
    where = args.out.relative_to(PACKAGE_ROOT) if inside else args.out
    fields = sum(len(op["fields"]) for op in operations)
    print(f"wrote {where} — {len(operations)} operations, {fields} documented fields\n")

    if CATALOGUE.exists():
        drift_report(operations, json.loads(CATALOGUE.read_text()))
    else:
        print(f"no {CATALOGUE.name} — run introspect_api.py for the drift report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
