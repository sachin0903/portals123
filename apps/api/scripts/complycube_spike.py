"""Answer the ComplyCube sandbox questions (S1–S5 in docs/aml.md) with a real `test_` key.

Run from apps/api once SCREENING_API_KEY (or COMPLYCUBE_API_KEY) holds a SANDBOX key:

    uv run python -m scripts.complycube_spike
    uv run python -m scripts.complycube_spike --front id-front.jpg --selfie selfie.jpg [--back b.jpg]

It refuses a key that does not start with `test_` unless --allow-live is given, so it never spends
live checks by accident. It writes
nothing to the database. Every raw ComplyCube answer goes to the report file (--out), which is
what docs/aml.md §10 is filled in from.

* AML (S2, S3): a client, an extensive screening, the matches, a validate attempt on the first
  match, the check again after it.
* Identity (S1, S5), only with --front and --selfie: a passport (or a licence with --back), a live
  photo, an identity check, and every key path in its result — the liveness and image-quality
  fields are read from that list.
* S4 and S6 need ComplyCube's webhooks, which are off while results arrive by polling.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from app.complycube.client import ScreeningClient
from app.complycube.sync import matches_from_check
from app.config import settings
from app.routers.aml import MATCH_OUTCOMES

POLL_EVERY_S = 3.0
POLL_FOR_S = 180.0


def refuse_live_key(key: str, *, allow_live: bool = False) -> str | None:
    """Why this key must not be used, or None when it may be: a sandbox key, or a live key the
    caller chose with --allow-live (each identity or screening check then uses a real one)."""
    if not key.strip():
        return "No key: set SCREENING_API_KEY (or COMPLYCUBE_API_KEY) to a sandbox test_ key."
    if key.startswith("live_") and allow_live:
        return None
    if not key.startswith("test_"):
        return (
            "Refusing a key that does not start with test_: this script only runs in the sandbox."
        )
    return None


def key_paths(value: Any, prefix: str = "") -> list[str]:
    """Every key path in a JSON value, e.g. ``breakdown.faceAnalysis.liveness``."""
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.append(path)
            paths.extend(key_paths(child, path))
    elif isinstance(value, list):
        for child in value:
            paths.extend(key_paths(child, f"{prefix}[]"))
    return sorted(set(paths))


def _error(exc: httpx.HTTPStatusError) -> dict[str, Any]:
    try:
        body: Any = exc.response.json()
    except ValueError:
        body = exc.response.text
    return {"status": exc.response.status_code, "body": body}


async def _settled(client: ScreeningClient, check_id: str) -> dict[str, Any]:
    """The check once ComplyCube stops reporting it pending, or as it stands at the time limit."""
    deadline = time.monotonic() + POLL_FOR_S
    while True:
        check = await client.get_check(check_id)
        if check.get("status") != "pending" or time.monotonic() > deadline:
            return dict(check)
        await asyncio.sleep(POLL_EVERY_S)


async def aml(client: ScreeningClient, args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {}
    person = await client.create_client(
        {
            "type": "person",
            "email": f"spike+{int(time.time())}@example.com",
            "personDetails": {
                "firstName": args.first,
                "lastName": args.last,
                "dob": args.dob,
                "nationality": args.nationality,
            },
        }
    )
    report["client"] = person
    started = await client.create_screening(str(person["id"]), monitoring=False)
    report["screening_started"] = started
    check = await _settled(client, str(started["id"]))
    report["screening_settled"] = check
    matches = matches_from_check(check)
    report["matches_read_by_portal"] = matches
    report["result_key_paths"] = key_paths(check.get("result"))
    if not matches:
        report["S2"] = "no match to validate; rerun with a name that matches (--first/--last)"
        return report
    first = matches[0]["provider_match_id"]
    try:
        # Exactly what the console sends for "Not a match" (app/routers/aml.py MATCH_OUTCOMES).
        report["S2_validate_match_response"] = await client.validate_check(
            str(started["id"]), MATCH_OUTCOMES["dismissed"], match_id=first, comment="Sandbox spike"
        )
    except httpx.HTTPStatusError as exc:
        report["S2_validate_match_error"] = _error(exc)
    report["S3_check_after_validate"] = await client.get_check(str(started["id"]))
    return report


async def identity(client: ScreeningClient, args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {}
    person = await client.create_client(
        {
            "type": "person",
            "email": f"spike-id+{int(time.time())}@example.com",
            "personDetails": {"firstName": args.first, "lastName": args.last},
        }
    )
    report["client"] = person
    document_type = "driving_license" if args.back else "passport"
    created = await client.create_document(
        str(person["id"]), document_type=document_type, issuing_country=args.issuing_country
    )
    report["document"] = created
    document_id = str(created["id"])

    def encoded(path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode("ascii")

    await client.upload_document_front(document_id, file_name="front.jpg", data=encoded(args.front))
    if args.back:
        await client.upload_document_back(
            document_id, file_name="back.jpg", data=encoded(args.back)
        )
    photo = await client.create_live_photo(str(person["id"]), data=encoded(args.selfie))
    report["live_photo"] = photo
    started = await client.create_identity_check(
        str(person["id"]), document_id=document_id, live_photo_id=str(photo["id"])
    )
    check = await _settled(client, str(started["id"]))
    report["identity_settled"] = check
    paths = key_paths(check.get("result"))
    report["S5_result_key_paths"] = paths
    report["S1_liveness_paths"] = [p for p in paths if "liveness" in p.lower()]
    return report


async def main(args: argparse.Namespace) -> int:
    key = settings.complycube_api_key
    refusal = refuse_live_key(key, allow_live=args.allow_live)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    client = ScreeningClient(key, base_url=settings.complycube_base_url)
    report: dict[str, Any] = {"base_url": settings.complycube_base_url}
    try:
        try:
            report["aml"] = await aml(client, args)
        except httpx.HTTPStatusError as exc:
            report["aml_error"] = _error(exc)
        if args.front and args.selfie:
            try:
                report["identity"] = await identity(client, args)
            except httpx.HTTPStatusError as exc:
                report["identity_error"] = _error(exc)
        else:
            report["identity"] = "skipped: pass --front and --selfie (and --back for a licence)"
        report["S4_S6"] = "skipped: these need ComplyCube's webhooks, which are off while polling"
    finally:
        await client.aclose()
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    aml_value = report.get("aml")
    id_value = report.get("identity")
    aml_part: dict[str, Any] = aml_value if isinstance(aml_value, dict) else {}
    id_part: dict[str, Any] = id_value if isinstance(id_value, dict) else {}
    print(f"Report written to {args.out}")
    print(f"  AML matches found: {len(aml_part.get('matches_read_by_portal', []))}")
    print(f"  S2 validate: {'error' if 'S2_validate_match_error' in aml_part else 'see report'}")
    if id_part:
        liveness = id_part.get("S1_liveness_paths") or []
        print(f"  S1 liveness fields: {', '.join(liveness) if liveness else 'none found'}")
    return 0


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--first", default="Vladimir")
    parser.add_argument("--last", default="Putin")
    parser.add_argument("--dob", default="1952-10-07")
    parser.add_argument("--nationality", default="RU")
    parser.add_argument("--issuing-country", default="GB")
    parser.add_argument("--front", type=Path)
    parser.add_argument("--back", type=Path)
    parser.add_argument("--selfie", type=Path)
    parser.add_argument("--out", type=Path, default=Path("complycube-spike.json"))
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="run with a live_ key; every check it starts is a real, counted check",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse())))
