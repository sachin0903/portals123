"""Entrypoint: ``python -m app`` (used by the ``api:dev`` task).

On Windows, psycopg's async driver refuses the default ProactorEventLoop, and uvicorn's own asyncio
loop setup re-asserts it — so we install the selector loop policy here (before importing uvicorn)
and pass ``loop='none'`` so uvicorn doesn't override it. Reload is disabled on Windows because
uvicorn's reload spawns a fresh subprocess that never runs this module, so it would recreate a
Proactor loop and crash. Linux/macOS get the normal auto loop + hot reload.

``timeout_graceful_shutdown=2`` is load-bearing for that reload. The platform tab holds
``GET /notifications/events`` (and the chat stream) open; uvicorn's default timeout is *None*,
so WatchFiles starts a reload, the worker logs "Waiting for connections to close", and
``:dev`` never comes back. Production does not use this module (the image runs ``uvicorn``
directly).
"""

import asyncio
import os
import sys
from pathlib import Path

_WIN = sys.platform == "win32"
if sys.platform == "win32":  # `sys.platform` (not the _WIN alias) so mypy platform-narrows this
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402  -- imported after the loop policy is set

_API_ROOT = Path(__file__).resolve().parent.parent

if __name__ == "__main__":
    # The platform keeps two SSE connections open per tab (`/messaging/events` and
    # `/notifications/events`). A reload calls shutdown on those connections, and for a
    # response that has not finished uvicorn only clears keep-alive — it does not close the
    # socket. With no timeout it then waits forever, which is the "Waiting for connections
    # to close" hang. The streams close themselves on the signal (`live_push.arm_server_stop`);
    # two seconds is the backstop for a request that is not a stream, then the new process can
    # bind. Watching `app/` only: a test edit must not restart it.
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8000"))
    if _WIN:
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            loop="none",
            reload=False,
            timeout_graceful_shutdown=2,
        )
    else:
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            loop="auto",
            reload=True,
            timeout_graceful_shutdown=2,
            reload_dirs=[os.path.dirname(os.path.abspath(__file__))],
        )
