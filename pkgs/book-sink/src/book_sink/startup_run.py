"""Start ``interlace serve --no-apply``, then enqueue one full ``POST /runs``.

``interlace serve`` applies the project once before the scheduler. That apply
refuses the whole plan when any model is breaking, and the daemon keeps serving
the previous warehouse. A run has no such gate: it rebuilds every model and
promotes the new definitions. Production sync is a run, so the boot apply is
off and this process enqueues the full DAG — the same ``POST /runs {}`` the
admin Refresh action uses — once ``/health`` reports ``ok``.

A run already in flight is left alone. A *succeeded* run enqueued inside the
debounce window is left alone too, so a moon file-save restart does not hammer
the custodian. Cron keeps firing on its own schedule after that.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

logger = logging.getLogger("book_sink.startup_run")

DEFAULT_DEBOUNCE = timedelta(minutes=30)
HEALTH_TIMEOUT = 180.0
HEALTH_POLL = 0.5
_IN_FLIGHT = frozenset({"queued", "running"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


class Http(Protocol):
    def request(
        self, method: str, url: str, *, body: bytes | None = None, timeout: float
    ) -> tuple[int, bytes]: ...


class UrlLibHttp:
    """Loopback urllib client. The daemon talks to itself, never to a vendor."""

    def request(
        self, method: str, url: str, *, body: bytes | None = None, timeout: float
    ) -> tuple[int, bytes]:
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(  # noqa: S310 — loopback, not caller-supplied
            url, data=body, method=method, headers=headers
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return int(response.status), response.read()


def env_enabled(value: str | None) -> bool:
    """Unset / blank defaults on. Explicit ``0`` / ``false`` / ``no`` / ``off`` is off."""
    if value is None or not value.strip():
        return True
    token = value.strip().lower()
    if token in _FALSY:
        return False
    if token in _TRUTHY:
        return True
    return True


def debounce_from_env(value: str | None) -> timedelta:
    try:
        seconds = int(value) if value not in (None, "") else int(DEFAULT_DEBOUNCE.total_seconds())
    except ValueError:
        seconds = int(DEFAULT_DEBOUNCE.total_seconds())
    return timedelta(seconds=max(0, seconds))


def bind_from_argv(
    argv: Sequence[str], *, default_host: str = "127.0.0.1", default_port: int = 8000
) -> tuple[str, int]:
    """Read ``--host`` / ``--port`` the way ``interlace serve`` does."""
    host, port = default_host, default_port
    pending: str | None = None
    for flag in argv:
        if pending == "host":
            host = flag
            pending = None
            continue
        if pending == "port":
            port = int(flag)
            pending = None
            continue
        if flag == "--host":
            pending = "host"
        elif flag.startswith("--host="):
            host = flag.split("=", 1)[1]
        elif flag == "--port":
            pending = "port"
        elif flag.startswith("--port="):
            port = int(flag.split("=", 1)[1])
    return host, port


def loopback_base(host: str, port: int) -> str:
    """``0.0.0.0`` is a bind address, not a URL a thread in this process can GET."""
    # ``0.0.0.0`` is what compose binds; we rewrite it, we do not listen on it here.
    wildcard = host in {"0.0.0.0", "::", "[::]"}  # noqa: S104
    loopback = "127.0.0.1" if wildcard else host
    return f"http://{loopback}:{port}"


def _parse_when(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def skip_reason(
    runs: Sequence[Mapping[str, object]],
    *,
    now: datetime,
    debounce: timedelta,
) -> str | None:
    """None means enqueue. queued/running is a live run, not a zombie."""
    if any(run.get("state") in _IN_FLIGHT for run in runs):
        return "in-flight"
    latest: datetime | None = None
    for run in runs:
        if run.get("state") != "succeeded":
            continue
        when = _parse_when(run.get("enqueued_at"))
        if when is not None and (latest is None or when > latest):
            latest = when
    if latest is not None and now - latest < debounce:
        return "debounced"
    return None


def serve_argv(argv: Sequence[str]) -> list[str]:
    """Flags for ``interlace serve``. The startup apply is always off."""
    args = [arg for arg in argv if arg != "--apply"]
    if "--no-apply" not in args:
        args.append("--no-apply")
    return args


def _health_ready(status: int, body: bytes) -> bool:
    """True when the daemon will accept a write.

    ``/health`` is HTTP 200 while a startup apply is still running, and a write
    during that window is 503. ``status: ok`` is the ready signal. A body that
    is not the health document falls back to the status code.
    """
    if status != 200:
        return False
    try:
        payload = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        return True
    if isinstance(payload, dict) and "status" in payload:
        reported = payload["status"]
        return isinstance(reported, str) and reported == "ok"
    return True


def wait_healthy(
    base_url: str,
    *,
    http: Http,
    wait: Callable[[float], None],
    timeout: float,
    poll: float,
    now: Callable[[], float],
) -> bool:
    deadline = now() + timeout
    while now() < deadline:
        try:
            status, body = http.request("GET", f"{base_url}/health", timeout=2.0)
            if _health_ready(status, body):
                return True
        except (OSError, urllib.error.URLError, TimeoutError):
            pass
        wait(poll)
    return False


def run_catch_up(
    base_url: str,
    *,
    enabled: bool,
    debounce: timedelta,
    now: datetime,
    http: Http,
    wait: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    health_timeout: float = HEALTH_TIMEOUT,
    health_poll: float = HEALTH_POLL,
) -> str:
    """Enqueue a full DAG, or return why not. A failed kick must not take serve down."""
    if not enabled:
        logger.info("startup run disabled")
        return "disabled"
    if not wait_healthy(
        base_url, http=http, wait=wait, timeout=health_timeout, poll=health_poll, now=clock
    ):
        logger.warning("startup run skipped: /health did not come up in %.0fs", health_timeout)
        return "unhealthy"
    try:
        status, body = http.request("GET", f"{base_url}/runs", timeout=10.0)
    except (OSError, urllib.error.URLError, TimeoutError):
        logger.exception("startup run skipped: GET /runs failed")
        return "http-error"
    if status != 200:
        logger.warning("startup run skipped: GET /runs returned %s", status)
        return "http-error"
    try:
        runs = json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("startup run skipped: GET /runs was not JSON")
        return "http-error"
    if not isinstance(runs, list):
        logger.warning("startup run skipped: GET /runs was not a list")
        return "http-error"
    reason = skip_reason(runs, now=now, debounce=debounce)
    if reason is not None:
        logger.info("startup run skipped: %s", reason)
        return reason
    try:
        status, body = http.request("POST", f"{base_url}/runs", body=b"{}", timeout=30.0)
    except (OSError, urllib.error.URLError, TimeoutError):
        logger.exception("startup run skipped: POST /runs failed")
        return "http-error"
    if status not in {200, 201}:
        logger.warning("startup run skipped: POST /runs returned %s", status)
        return "http-error"
    try:
        payload = json.loads(body.decode())
        models = payload.get("models") if isinstance(payload, dict) else None
        count = len(models) if isinstance(models, list) else "?"
    except (UnicodeDecodeError, json.JSONDecodeError):
        count = "?"
    logger.info("startup run enqueued %s models", count)
    return "enqueued"


def serve(flag_env: str, argv: Sequence[str] | None = None) -> None:
    """Start the catch-up thread, then hand off to ``interlace serve --no-apply``."""
    args = serve_argv(list(sys.argv[1:] if argv is None else argv))
    host, port = bind_from_argv(args)
    base = loopback_base(host, port)
    enabled = env_enabled(os.environ.get(flag_env))
    debounce = debounce_from_env(os.environ.get("SYNC_ON_START_DEBOUNCE_SECONDS"))

    def _kick() -> None:
        try:
            run_catch_up(
                base,
                enabled=enabled,
                debounce=debounce,
                now=datetime.now(UTC),
                http=UrlLibHttp(),
            )
        except Exception:
            logger.exception("startup run failed")

    threading.Thread(target=_kick, name="startup-run", daemon=True).start()
    from interlace.cli import app

    app(args=["serve", *args])


def main() -> None:
    """Process entry: ``python -m book_sink.startup_run --host … --port …``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    serve("SYNC_ON_START")


if __name__ == "__main__":
    main()
