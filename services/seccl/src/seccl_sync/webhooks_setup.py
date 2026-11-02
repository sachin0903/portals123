"""Register the gateway with Seccl's outgoing webhooks.

    python -m seccl_sync.webhooks_setup [--kinds transaction,position,...]

Each kind is configured with PATCH /webhook/config/{kind}/{firmId} pointing at
``{SECCL_WEBHOOK_URL}/webhooks/seccl/{kind}`` signed with SECCL_WEBHOOK_SECRET,
then the firm settings are printed to confirm what Seccl now reports.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from seccl_sync.client import SecclClient
from seccl_sync.gateway import KIND_TO_STREAM
from seccl_sync.settings import SecclSettings


async def register(kinds: list[str]) -> int:
    settings = SecclSettings()
    if not settings.webhook_url or not settings.webhook_secret:
        print("SECCL_WEBHOOK_URL and SECCL_WEBHOOK_SECRET must be set", file=sys.stderr)
        return 1
    async with SecclClient(settings) as api:
        for kind in kinds:
            await api.request_json(
                "PATCH",
                f"/webhook/config/{kind}/{api.firm_id}",
                json={
                    "url": f"{settings.webhook_url.rstrip('/')}/webhooks/seccl/{kind}",
                    "secret": settings.webhook_secret,
                },
            )
            print(f"configured {kind} webhook")
        firm = await api.request_json("GET", f"/settings/{api.firm_id}")
        data = firm.get("data", firm) if isinstance(firm, dict) else {}
        for key, value in sorted(data.items() if isinstance(data, dict) else []):
            if "webhook" in key.lower():
                print(f"  {key}: {value}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kinds",
        default=",".join(KIND_TO_STREAM),
        help=f"comma-separated webhook kinds (default: all of {','.join(KIND_TO_STREAM)})",
    )
    args = parser.parse_args()
    kinds = [kind.strip() for kind in args.kinds.split(",") if kind.strip()]
    unknown = [kind for kind in kinds if kind not in KIND_TO_STREAM]
    if unknown:
        parser.error(f"unknown kinds: {unknown}; valid: {sorted(KIND_TO_STREAM)}")
    return asyncio.run(register(kinds))


if __name__ == "__main__":
    raise SystemExit(main())
