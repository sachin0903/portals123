"""Transcribe SEI's API sheet into `sei/vendor_manifest.json`.

SEI documents their web services as an EXCEL WORKBOOK issued per firm ("Select-Marlborough API
sheet"), one row per service with a sample request and a sample response. There is no schema:
the only statement of what a body contains is the example. So the field contract is INFERRED
FROM THAT EXAMPLE -- exactly the posture `pkgs/seccl/scripts/build_schemas.py` takes for a vendor
who ships a Postman collection, and for the same reason: a name a human retypes becomes a 400
from a live custodian, or worse a silently wrong instruction.

    uv run python scripts/build_manifest.py "~/dev/sei/Select-Marlborough_API_sheet_v2 151025.xlsx"

WHAT INFERENCE MEANS HERE, and it is weaker than Morgan Lloyd's:

  * **required is not known.** The sheet has no Required column, so every field is optional and
    the console cannot pre-empt a vendor rejection the way it does for ML. Stated rather than
    guessed -- marking them all required would block legitimate partial calls, and marking a
    guess as knowledge is worse than the gap.
  * **types come from the sample VALUE**, so a string that happens to hold digits reads as a
    string and an integer sample of a field that accepts decimals reads as int.
  * **one example is one shape.** A field absent from the sample is absent here.

THE WORKBOOK IS NOT COMMITTED and neither are its credentials: the "User Details" and "IP&Port"
sheets carry a live username, password and allowlisted IP, and this script reads ONLY the "API
Details" sheet. The generated manifest carries no credential and no URL host.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

OUTPUT = Path(__file__).resolve().parents[1] / "src" / "sei" / "vendor_manifest.json"

# The APIGEE host differs per environment and the sheet is issued against test. Only the service
# PATH is portable, so only the path is kept -- a manifest carrying `test.mtls.api.seic.com`
# would be a production deployment's silent misconfiguration.
# `//api/` appears in the sheet as well as `/api/`, so the separator is `/+`. A near-miss here
# leaves the host in the operation name, which then differs per environment.
_ROOT = re.compile(r"^https?://[^/]+/+api/v\d+/swp/", re.IGNORECASE)
# Sample paths carry real ids (`enrollment/3106/ENR-23904`). Kept out of the operation NAME so
# two services do not look different because one sample used a different account.
_SEGMENT = re.compile(r"^[A-Z]{2,}-?\d+$|^\d+$")


def _service_path(url: str) -> str:
    stripped = _ROOT.sub("", (url or "").strip()).split("?")[0].strip().rstrip("/")
    kept = [s for s in stripped.split("/") if s and not _SEGMENT.match(s)]
    return "/".join(kept)


def _kind(value: Any) -> str:
    """The type a sample value implies. `bool` before `int`, since bool IS an int in Python."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "decimal"
    return "string"


def _leaves(node: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Every scalar in the body, as a dotted path.

    A list contributes its FIRST element under `name[]`: SEI's arrays in these bodies are
    homogeneous (a list of accounts, a list of tags), so one element states the shape. Where the
    array is the point of the call -- enrolment's `clientList` -- the caller refuses the whole
    operation rather than pretending one element is a form.
    """
    if isinstance(node, dict):
        out: list[tuple[str, Any]] = []
        for key, value in node.items():
            out += _leaves(value, f"{prefix}.{key}" if prefix else key)
        return out
    if isinstance(node, list):
        return _leaves(node[0], f"{prefix}[]") if node else [(prefix, "")]
    return [(prefix, node)]


def parse(path: Path) -> dict[str, Any]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True)
    sheet = workbook["API Details"]
    rows = list(sheet.iter_rows(values_only=True))
    header = [str(c).strip() if c else "" for c in rows[0]]

    services: list[dict[str, Any]] = []
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        record = {header[i]: row[i] for i in range(min(len(header), len(row)))}
        name = str(record.get("Web Service -Name") or "").strip()
        url = str(record.get("APIGEE-URL") or "").strip()
        if not name or not url:
            continue
        service: dict[str, Any] = {
            "name": name,
            "method": str(record.get("API-Method") or "").strip().upper(),
            "path": _service_path(url),
            "category": str(record.get("DevPortal - Browse Product Category") or "").strip(),
            "product": str(record.get("DevPortal - APP Creation Product Name") or "").strip(),
        }
        raw = str(record.get("Sample Request") or "").strip()
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        if isinstance(body, dict):
            leaves = _leaves(body)
            service["fields"] = [
                {"name": n, "type": _kind(v)} for n, v in leaves if n and "[]" not in n
            ]
            # Named, not dropped: a flat form cannot build these, and the caller has to be able
            # to say which parts of the body it is not offering.
            service["repeating"] = sorted({n.split("[]")[0] + "[]" for n, _ in leaves if "[]" in n})
            service["depth"] = max((n.count(".") for n, _ in leaves), default=0) + 1
        services.append(service)
    return {"source": path.name, "services": services}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", help="path to the SEI API sheet (.xlsx)")
    parser.add_argument("--check", action="store_true", help="exit 1 if the manifest would change")
    args = parser.parse_args(argv)

    path = Path(args.workbook).expanduser()
    if not path.exists():
        print(f"no such workbook: {path}", file=sys.stderr)
        return 2

    manifest = parse(path)
    rendered = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    with_fields = sum(1 for s in manifest["services"] if s.get("fields"))
    print(f"{len(manifest['services'])} services, {with_fields} with a sample body")

    if args.check:
        current = OUTPUT.read_text("utf-8") if OUTPUT.exists() else ""
        if current != rendered:
            print("manifest is STALE — re-run without --check", file=sys.stderr)
            return 1
        print("manifest is up to date")
        return 0

    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
