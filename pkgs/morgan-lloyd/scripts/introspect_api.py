"""Build the Morgan Lloyd action catalogue from the live Dataverse CSDL.

`$metadata` is the only complete, authoritative description of this org's API: it lists every
`cam_*` action with its exact parameter names and types. Our hand-written
`docs/morgan-lloyd/api-reference.md` undercounts (47 actions), the client undercounts (51), and
the org actually exposes 86 — and the doc's action NAMES disagree with the client's in ten
places. So the catalogue is generated, never typed by hand.

    uv run python scripts/introspect_api.py            # fetch live + write the catalogue
    uv run python scripts/introspect_api.py --metadata /path/to/$metadata.xml

Output: `catalogue.json` beside the package — {action: {params: [{name, type, collection}],
returns}} — plus a coverage report against the client on stdout.

What CSDL does NOT give you, and why the scrape in `enrich_catalogue.py` is still needed:
  * every action parameter is marked `Nullable="true"` regardless of business requirement, so
    required-vs-optional is absent;
  * option-set parameters are bare `Int32` with no labels (hence `morgan_lloyd/enums.py`);
  * no descriptions, no examples.
CSDL owns names and types. The docs site owns semantics. Neither alone is sufficient.

Requires Morgan Lloyd credentials (see `morgan_lloyd.settings`). Reaching Dataverse needs an
Azure AD token, which Conditional Access may refuse from an unallowlisted network — pass
`--metadata` with a saved copy in that case.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = PACKAGE_ROOT / "catalogue.json"
ENDPOINTS = PACKAGE_ROOT / "src" / "morgan_lloyd" / "endpoints"

# CSDL is one line of 31MB, so stream over element matches rather than parsing a DOM.
_ACTION = re.compile(
    r'<Action Name="(?P<name>cam_[^"]+)"(?P<attrs>[^>]*)>(?P<body>.*?)</Action>', re.S
)
_PARAM = re.compile(r'<Parameter Name="(?P<name>[^"]+)" Type="(?P<type>[^"]+)"')
_RETURN = re.compile(r'<ReturnType Type="(?P<type>[^"]+)"')
# What the client actually POSTs — the authoritative spelling.
_CALL_ACTION = re.compile(r'call_action\(\s*"([^"]+)"')

_METADATA_PATH = "$metadata"


async def fetch_metadata() -> str:
    """GET $metadata. Needs `Accept: application/xml` — the client defaults to JSON and
    Dataverse answers 415 "A supported MIME type could not be found" for XML-only resources."""
    import httpx

    from morgan_lloyd.client import MorganLloydClient
    from morgan_lloyd.settings import MorganLloydSettings

    settings = MorganLloydSettings()
    async with MorganLloydClient(settings=settings) as api:
        headers = {**(await api._auth_headers()), "Accept": "application/xml"}
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.get(
                f"{settings.api_base_url}/{_METADATA_PATH}", headers=headers
            )
    response.raise_for_status()
    return response.text


def _simplify(odata_type: str) -> tuple[str, bool]:
    """('String', False) / ('cam_scheme', True) — bare name, and whether it is a collection."""
    collection = odata_type.startswith("Collection(")
    inner = odata_type[len("Collection(") : -1] if collection else odata_type
    return inner.replace("mscrm.", "").replace("Edm.", ""), collection


def parse_actions(metadata: str) -> dict[str, dict[str, Any]]:
    catalogue: dict[str, dict[str, Any]] = {}
    for match in _ACTION.finditer(metadata):
        body = match["body"]
        returns = _RETURN.search(body)
        catalogue[match["name"]] = {
            "params": [
                {
                    "name": p["name"],
                    "type": _simplify(p["type"])[0],
                    "collection": _simplify(p["type"])[1],
                }
                for p in _PARAM.finditer(body)
            ],
            "returns": _simplify(returns["type"])[0] if returns else None,
            "bound": 'IsBound="true"' in match["attrs"],
        }
    return catalogue


def implemented_actions() -> set[str]:
    return {
        action
        for path in ENDPOINTS.glob("*.py")
        for action in _CALL_ACTION.findall(path.read_text())
    }


def report(catalogue: dict[str, dict[str, Any]]) -> None:
    have = implemented_actions()
    known = set(catalogue)
    print(f"actions in $metadata     : {len(known)}")
    print(f"implemented by the client: {len(have)}")
    missing = sorted(known - have)
    unknown = sorted(have - known)
    print(f"\nIN THE ORG, NOT IMPLEMENTED ({len(missing)}):")
    for action in missing:
        params = ", ".join(p["name"] for p in catalogue[action]["params"][:6])
        print(f"   {action:44} ({params}{'…' if len(catalogue[action]['params']) > 6 else ''})")
    if unknown:
        # A client action absent from metadata is either a typo or a since-removed action —
        # either way it will 404 at runtime, so this list should always be empty.
        print(f"\nIMPLEMENTED BUT NOT IN THE ORG ({len(unknown)}) — these will fail:")
        for action in unknown:
            print(f"   {action}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, help="read a saved $metadata instead of fetching")
    parser.add_argument("--out", type=Path, default=CATALOGUE)
    args = parser.parse_args(argv)

    metadata = (
        args.metadata.read_text(errors="replace")
        if args.metadata
        else asyncio.run(fetch_metadata())
    )
    catalogue = parse_actions(metadata)
    if not catalogue:
        print("no cam_* actions found — is this really a Dataverse $metadata document?")
        return 1
    args.out.write_text(json.dumps(catalogue, indent=2, sort_keys=True) + "\n")
    where = (
        args.out.relative_to(PACKAGE_ROOT) if args.out.is_relative_to(PACKAGE_ROOT) else args.out
    )
    params = sum(len(a["params"]) for a in catalogue.values())
    print(f"wrote {where} — {len(catalogue)} actions, {params} parameters\n")
    report(catalogue)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
