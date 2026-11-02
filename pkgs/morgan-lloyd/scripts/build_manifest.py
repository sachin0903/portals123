"""Transcribe the Morgan Lloyd vendor documentation into `morgan_lloyd/vendor_manifest.json`.

WHY A GENERATOR AND NOT A HAND-WRITTEN FILE. The manifest drives two things that must not be
approximated: the columns the admin entity tables show, and the forms that build real payloads
POSTed to a live Dynamics org. A field name invented or mistyped by a human -- or by a model
summarising the page -- produces a 400 from the vendor at best and a silently wrong record at
worst. So the page is PARSED, deterministically, and the parse is re-runnable: `--check` says
whether the vendor has changed the contract under us, which is otherwise invisible until a call
fails in production.

The source is `https://dev.api.morgan-lloyd.co.uk/` -- a public single-page Slate document, the
same one `enums.py` was transcribed from by hand. This mirrors
`pkgs/seccl/scripts/build_schemas.py`, which does the equivalent job for a vendor who ships a
Postman collection instead of a spec.

    uv run python scripts/build_manifest.py                 # fetch, write the manifest
    uv run python scripts/build_manifest.py --check         # fetch, diff, exit 1 on drift
    uv run python scripts/build_manifest.py --from-file x.html

WHAT IT DOES NOT DO: it does not invent types, infer requiredness, or normalise the vendor's
names. Everything in the output appears verbatim in the page. Where the doc and the client
disagree on an action's NAME, the client is authoritative (docs/morgan-lloyd/endpoint-coverage.md
§1 lists the eleven) -- that reconciliation lives in `morgan_lloyd/manifest.py`, not here, so
this file stays a pure transcription.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

DOC_URL = "https://dev.api.morgan-lloyd.co.uk/"

# INSIDE the package, not under docs/. The API reads this at run time to build the entity tables
# and the action forms, and a wheel does not ship the repo's docs directory -- the same trap
# `block-contract.json` documents from the other side (the API container has no pkgs/ui, so the
# widget contract had to be mirrored in Python). Here the package IS an API dependency, so the
# file can simply travel with it. docs/morgan-lloyd/README.md points at it.
OUTPUT = Path(__file__).resolve().parents[1] / "src" / "morgan_lloyd" / "vendor_manifest.json"

_HEADING = re.compile(r"<h([12])[^>]*id=['\"]([^'\"]+)['\"][^>]*>(.*?)</h\1>", re.S)
# Any level, for classifying the tables INSIDE an operation ("Fields", "Response Fields",
# "URL Parameters", "Body Data").
_SUB_HEADING = re.compile(r"<h[1-6][^>]*>.*?</h[1-6]>", re.S)
_TABLE = re.compile(r"<table>.*?</table>", re.S)
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
# ANY highlighted block, not just `highlight http`. The doc tags most request blocks `http` but
# some `plaintext` -- "Create Investment" is one, and requiring the http class silently gave it
# no action at all, which reads in the output as "this operation is undocumented" when in fact it
# is `cam_createwrapper`.
_CODE_BLOCK = re.compile(r'<pre class="highlight [^"]*"><code>(.*?)</code></pre>', re.S)
_REQUEST_LINE = re.compile(r"^\s*(GET|POST|PATCH|PUT|DELETE)\s+(\S+)", re.M)
# Everything up to and including the API root, in either spelling the doc uses.
_API_ROOT = re.compile(r"^(?:https?://[^/]+)?/?(?:api/data/v9\.1/)?", re.IGNORECASE)

# The vendor introduces an option set in prose, and the introducer is not stable: "Allowed
# Values", "Accepted Values", "Possible Values", a bare "Values:", and -- once --
# "Possible Values: Values (". So the ANCHOR IS THE WORD `values` IMMEDIATELY BEFORE THE OPEN
# PAREN, with any qualifier optional, rather than a list of the phrasings seen so far.
#
# Naming only two of them missed ELEVEN documented option sets, ten of them on
# `cam_drawdownsetup`: `drawdownOption`, `incomeFrequency`, `investmentPathway`, `paymentDay` and
# the six guidance/advice attendance flags all use "Values:" or "Accepted Values:". They parsed
# as fields with no options, which renders as a FREE TEXT BOX for a closed set -- so the whole
# drawdown instruction, the one action here that moves a client's money, was typed by hand with
# no validation on either side (`platform_actions.validate` can only check a set it has).
#
# GREEDY to the last `)`, not `[^)]*` to the first. Two labels in the vulnerability-
# characteristics set contain their own parentheses -- "Domestic abuse (including economic
# control)" and "Inadequate (outgoings exceed income) or erratic income" -- and stopping at the
# first close paren silently truncated that set from 24 members to 11. A short option set is not
# an error anywhere downstream: it renders as a valid-looking dropdown that is missing half its
# choices, and the vendor rejects the value the user could not pick.
_ALLOWED = re.compile(
    r"(?:allowed|accepted|possible|valid)?\s*values\s*:?\s*\((.*)\)", re.S | re.IGNORECASE
)
# A multiselect is sent as a comma-separated STRING of members, not as one value, and the page
# says so in the description. Marked here rather than inferred from the type, because the type
# on those fields is `string` exactly like a free-text name -- indistinguishable without this.
_MULTISELECT = re.compile(r"multi\s*-?\s*select", re.IGNORECASE)


def _text(fragment: str) -> str:
    """Tag-stripped, entity-decoded, whitespace-collapsed."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _options(description: str) -> list[dict[str, Any]] | None:
    """The option set named in a Description cell, or None.

    ANCHORED ON THE NUMBERS, not on the commas, and that is the whole subtlety. The vendor
    writes entries three ways -- `Mr : 809130000`, `Health 809130000` and a bare `809130000` --
    and one label legitimately CONTAINS commas:

        Other circumstances that affect people's experience of financial services e.g., leaving
        care, migration or seeking asylum, human trafficking or modern slavery, convictions
        809130012

    Splitting on ',' shreds that into five bogus members. Matching label-then-number left to
    right keeps it whole, because each entry is terminated by its value.

    A LIST OF {label, value}, not a dict, because `cam_taxrelief` documents its four values with
    NO labels at all -- their text lives in a separate Tax Relief Lookup table, captured as
    `lookups`. A dict would have to invent a key for those, and inventing is the one thing this
    file must not do. `label` is null there, and a form renders the lookup instead.
    """
    found = _ALLOWED.search(description)
    if not found:
        return None
    body = found.group(1)
    options: list[dict[str, Any]] = []
    for match in re.finditer(r"\s*(?P<label>.*?)\s*:?\s*(?P<value>\d+)\s*(?:,|$)", body, re.S):
        label = match.group("label").strip(" ,:") or None
        options.append({"label": label, "value": int(match.group("value"))})
    return options or None


def _action(path: str) -> str:
    """The Dataverse action or entity set a request line names.

    The doc writes the target three ways in the same page -- `/cam_createcontact`, the whole
    `https://…/api/data/v9.1/cam_getadvisercontacts`, and `contacts(fcdab33b-…)` with a sample
    GUID -- so the raw path is kept as `path` and this is the comparable name.

    CASE IS PRESERVED. Dynamics action names are case sensitive and
    `cam_CreateFinancialAdviserContact` is genuinely mixed-case; lower-casing for tidiness would
    produce an action name that 404s.
    """
    stripped = _API_ROOT.sub("", path.strip())
    # Drop a sample key -- `contacts(fcdab33b-…)` is the `contacts` entity set.
    return stripped.split("(")[0].split("?")[0].rstrip("/")


# What the heading above a table says it is. The page is a Slate document and labels every one:
# "Fields" / "Body Data" (the request), "Response Fields", "URL Parameters", and a handful of
# "<thing> JSON" tables describing a nested request object.
_RESPONSE_HEADING = re.compile(r"response", re.IGNORECASE)
_PARAMETER_HEADING = re.compile(r"url parameter", re.IGNORECASE)


def _table_kind(heading: str, header: list[str]) -> str:
    """Whether a field table is the request, the response, or URL parameters.

    **KEYED ON THE HEADING**, because neither reading order nor the column shape is reliable and
    both were tried:

    * order -- "the first field table is the request" put every PATCH's URL-parameter table
      (`GUID`) in `fields` and pushed the real contract into `response_fields`, so Update Transfer
      offered one field called GUID instead of the three the vendor documents, and the action
      forms papered over it by borrowing the create's 23 fields, which that endpoint does not take;
    * a `Required` column -- true of 54 request tables and FALSE of three that are still requests
      (Update Beneficiary, and the token call's Headers/Authentication), which would have been
      silently emptied instead.

    The heading is the document's own answer and covers all 63.
    """
    if _PARAMETER_HEADING.search(heading) or (header and header[0] == "parameter"):
        return "parameter"
    return "response" if _RESPONSE_HEADING.search(heading) else "request"


def _fields(table: str) -> list[dict[str, Any]] | None:
    """Rows of a field table, or None if it is not one."""
    rows = _ROW.findall(table)
    if not rows:
        return None
    header = [_text(c).lower() for c in _CELL.findall(rows[0])]
    # NOT "key": a Key/Value table is a LOOKUP, not a field list. Accepting it here filed the
    # Tax Relief lookup as Create Contact's response fields -- the operation then claimed to
    # return four fields called "809130000".. and the lookup, which is the only place those
    # values' meanings exist, was reachable nowhere.
    if not header or header[0] not in ("field", "parameter"):
        return None

    fields: list[dict[str, Any]] = []
    for row in rows[1:]:
        cells = [_text(c) for c in _CELL.findall(row)]
        if len(cells) < 2:
            continue
        record: dict[str, Any] = {"name": cells[0]}
        index = {name: position for position, name in enumerate(header)}
        for column in ("type", "required", "description"):
            position = index.get(column)
            if position is not None and position < len(cells):
                record[column] = cells[position]
        # The vendor marks conditionally-required fields "No*" / "No**" and explains the
        # condition in a footnote. Kept verbatim in `required` AND reduced to a boolean, so a
        # form can gate on the boolean without the caveat being lost.
        raw = str(record.get("required", ""))
        record["required_flag"] = raw.strip().lower().startswith("yes")
        description = str(record.get("description", ""))
        options = _options(description)
        if options:
            record["options"] = options
            # Only meaningful alongside an option set, so not emitted without one.
            if _MULTISELECT.search(description):
                record["multiselect"] = True
        fields.append(record)
    return fields or None


def _lookup(table: str) -> dict[str, str] | None:
    """A two-column Key/Value lookup (the Tax Relief table is the one that matters)."""
    rows = _ROW.findall(table)
    if not rows:
        return None
    header = [_text(c).lower() for c in _CELL.findall(rows[0])]
    if header[:2] != ["key", "value"]:
        return None
    out: dict[str, str] = {}
    for row in rows[1:]:
        cells = [_text(c) for c in _CELL.findall(row)]
        if len(cells) >= 2:
            out[cells[0]] = cells[1]
    return out or None


def parse(page: str) -> dict[str, Any]:
    """The whole document as {sections: [{title, operations: [...]}]}."""
    # Drop the navigation: its <a class="toc-h2"> entries are not headings, but a naive scan
    # for titles would pick the TOC up twice.
    start = page.find("<h1")
    body = page[start:] if start >= 0 else page

    marks = [
        (m.start(), int(m.group(1)), m.group(2), _text(m.group(3))) for m in _HEADING.finditer(body)
    ]
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for index, (position, level, anchor, title) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(body)
        chunk = body[position:end]

        if level == 1:
            current = {"title": title, "anchor": anchor, "operations": []}
            sections.append(current)
            continue
        if current is None:
            continue

        operation: dict[str, Any] = {"title": title, "anchor": anchor}
        for block in _CODE_BLOCK.finditer(chunk):
            request_line = _REQUEST_LINE.search(_text_lines(block.group(1)))
            if not request_line:
                continue
            operation["method"] = request_line.group(1)
            operation["path"] = request_line.group(2)
            operation["action"] = _action(request_line.group(2))
            break

        # Headings and tables in document order, so each table can be classified by the one above
        # it. Any level: the sub-headings inside an operation are h3/h4, while `_HEADING` matches
        # only the h1/h2 that delimit sections and operations.
        marked = sorted(
            [(m.start(), "h", _text(m.group(0))) for m in _SUB_HEADING.finditer(chunk)]
            + [(m.start(), "t", m.group(0)) for m in _TABLE.finditer(chunk)]
        )
        heading = ""
        for _, mark, fragment in marked:
            if mark == "h":
                heading = fragment
                continue
            # Lookup first: it is the more specific shape (exactly Key/Value), and a table that is
            # both would otherwise be claimed by whichever test ran first.
            lookup = _lookup(fragment)
            if lookup:
                operation.setdefault("lookups", []).append(lookup)
                continue
            rows = _fields(fragment)
            if not rows:
                continue
            header = [_text(c).lower() for c in _CELL.findall(_ROW.findall(fragment)[0])]
            kind = _table_kind(heading, header)
            if kind == "parameter":
                # Path segments, not a body. Kept, because `GUID` is genuinely part of the
                # contract -- just not part of the payload a form builds.
                operation.setdefault("parameters", []).extend(rows)
            elif kind == "request" and "fields" not in operation:
                operation["fields"] = rows
            elif kind == "request":
                # A nested request object ("Fund Splits JSON" and friends). Kept under its own
                # heading rather than dropped: a form cannot render it, but losing it silently is
                # how the shape of `policyValuation` would have to be rediscovered by hand.
                operation.setdefault("nested", {})[heading] = rows
            elif "response_fields" not in operation:
                operation["response_fields"] = rows
        current["operations"].append(operation)

    return {"source": DOC_URL, "sections": sections}


def _text_lines(fragment: str) -> str:
    """Tag-stripped but NEWLINE-PRESERVING — the request line is the first line of the block,
    and `_text`'s whitespace collapse would run it into the Host header."""
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def fetch(url: str) -> str:
    if not url.startswith("https://"):
        # S310 wants the scheme checked rather than assumed. Worth honouring rather than
        # silencing: this is a developer tool whose output is committed, and a `file:` URL
        # slipped into the default would write a manifest from an attacker-supplied page.
        raise ValueError(f"refusing to fetch a non-https URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "graphene-manifest/1.0"})  # noqa: S310 - checked above
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - checked above
        return str(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="diff against the checked-in file")
    parser.add_argument("--from-file", type=Path, help="parse a saved copy instead of fetching")
    args = parser.parse_args()

    page = args.from_file.read_text(encoding="utf-8") if args.from_file else fetch(DOC_URL)
    manifest = parse(page)
    rendered = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"

    operations = sum(len(s["operations"]) for s in manifest["sections"])
    documented = sum(1 for s in manifest["sections"] for o in s["operations"] if o.get("fields"))
    print(
        f"{len(manifest['sections'])} sections, {operations} operations, "
        f"{documented} with field tables"
    )

    if args.check:
        if not OUTPUT.exists():
            print(f"{OUTPUT} does not exist", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != rendered:
            print(
                f"{OUTPUT} is out of date with the vendor documentation. "
                "Re-run without --check and review the diff: a change here is a change to a "
                "contract we send real payloads against.",
                file=sys.stderr,
            )
            return 1
        print("manifest is up to date")
        return 0

    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
