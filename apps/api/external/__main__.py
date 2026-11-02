"""Entrypoint: ``python -m external``."""

from __future__ import annotations

import asyncio
import os
import sys

_WIN = sys.platform == "win32"
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402
from app.logging import init_tracing, shutdown_tracing  # noqa: E402
from external.app import create_app  # noqa: E402


def main() -> None:
    init_tracing("portals-external")
    stub = os.getenv("EXTERNAL_STUB", "true").lower() not in {"0", "false", "no"}
    try:
        uvicorn.run(
            create_app(
                stub=stub,
                screening_secret=settings.complycube_webhook_secret,
            ),
            host=os.getenv("EXTERNAL_HOST", "127.0.0.1"),
            port=int(os.getenv("EXTERNAL_PORT", "8010")),
            loop="none" if _WIN else "auto",
        )
    finally:
        shutdown_tracing()


if __name__ == "__main__":
    main()
