"""
Central webhook server — auto-discovers and mounts all provider webhooks.

Each provider that has a webhook.py exposing a `router` (APIRouter) is
automatically mounted at /{provider}. If a provider also exposes a
`register()` function it is called once on startup.

Routes:
  GET  /{provider}/webhook   — subscription verification
  POST /{provider}/webhook   — receive payload
  GET  /health               — health check
"""

import importlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

logger = logging.getLogger(__name__)

_register_callbacks: list = []


def _discover_routers(app: FastAPI) -> None:
    providers_dir = Path(__file__).parent.parent / "providers"

    for provider_dir in sorted(providers_dir.iterdir()):
        if not provider_dir.is_dir():
            continue

        webhook_file = provider_dir / "webhook.py"
        if not webhook_file.exists():
            continue

        provider = provider_dir.name
        module_path = f"intelliflo_sync.providers.{provider}.webhook"

        try:
            module = importlib.import_module(module_path)

            router = getattr(module, "router", None)
            if router is None:
                logger.warning("Skipping %s: webhook.py has no `router`", provider)
                continue

            app.include_router(router, prefix=f"/{provider}")
            logger.info("Mounted webhook: /%s", provider)

            register_fn = getattr(module, "register", None)
            if callable(register_fn):
                _register_callbacks.append((provider, register_fn))
                logger.info("Queued register() for: %s", provider)

        except Exception as e:
            logger.error("Failed to load webhook for %s: %s", provider, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No Postgres migration step here: the `integrations` schema is created by apps/api's own
    # migrations, which always run ahead of this service's deploy.
    for provider, register_fn in _register_callbacks:
        try:
            logger.info("Registering webhook for provider: %s", provider)
            register_fn()
        except Exception as e:
            logger.error("Webhook registration failed for %s: %s", provider, e)
    yield


app = FastAPI(title="Integration Engine Webhook", lifespan=lifespan)

_discover_routers(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


def start():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    host = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.getenv("WEBHOOK_PORT", "4000"))
    logger.info("Starting central webhook server on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
