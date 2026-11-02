"""The verification registry reloads itself on a miss.

The map is per API process and there is one process per site. The admin call that registers a
firm refreshed only the replica that served it, so the others verified that firm's tokens against
the wrong keys until restarted — and because the browser is load-balanced across sites, a correct
password and TOTP code worked on one site and failed on the other two. A miss now reloads from the
database, throttled and single-flight; a hit never reads.
"""

import asyncio
import time

import pytest

from app import tenant_registry

ENV_A = tenant_registry.EnvConfig(
    tenant_id=1, stage="production", workos_client_id="client_a", issuer="https://iss/a"
)
ENV_B = tenant_registry.EnvConfig(
    tenant_id=2, stage="production", workos_client_id="client_b", issuer="https://iss/b"
)


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenant_registry, "_by_org", {"org_a": ENV_A})
    monkeypatch.setattr(tenant_registry, "_by_issuer", {ENV_A.issuer: ENV_A})
    monkeypatch.setattr(tenant_registry, "_last_reload", float("-inf"))
    monkeypatch.setattr(tenant_registry, "_reload_lock", None)


def _stub_load(monkeypatch: pytest.MonkeyPatch, rows_after: dict[str, tenant_registry.EnvConfig]):
    """Replace the DB read with one that installs ``rows_after`` as the org map and counts calls.
    Stamps ``_last_reload`` the way the real ``load`` does, since that is what the throttle keys
    on."""
    calls: list[int] = []

    async def fake_load() -> int:
        calls.append(1)
        tenant_registry._last_reload = time.monotonic()
        tenant_registry._by_org = dict(rows_after)
        return len(rows_after)

    monkeypatch.setattr(tenant_registry, "load", fake_load)
    return calls


async def test_hit_never_reloads(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_load(monkeypatch, {})
    assert await tenant_registry.resolve("org_a", None) is ENV_A
    assert await tenant_registry.resolve(None, ENV_A.issuer) is ENV_A
    assert calls == []


async def test_miss_reloads_and_finds_a_newly_registered_firm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production failure: a firm inserted after this process booted. The first token from it
    is a miss, the miss reloads, and the token verifies against the right environment."""
    calls = _stub_load(monkeypatch, {"org_a": ENV_A, "org_b": ENV_B})
    assert await tenant_registry.resolve("org_b", None) is ENV_B
    assert len(calls) == 1


async def test_a_nothing_token_does_not_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    """No org and no issuer is not a stale-map signal — it is a token with nothing to look up."""
    calls = _stub_load(monkeypatch, {})
    assert await tenant_registry.resolve(None, None) is None
    assert calls == []


async def test_repeated_misses_are_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garbage tokens must not become one query per request: a second miss inside the interval is
    answered from memory. Past the interval it reloads again."""
    calls = _stub_load(monkeypatch, {"org_a": ENV_A})
    assert await tenant_registry.resolve("org_unknown", None) is None
    assert await tenant_registry.resolve("org_unknown", None) is None
    assert len(calls) == 1
    monkeypatch.setattr(tenant_registry, "_last_reload", float("-inf"))
    assert await tenant_registry.resolve("org_unknown", None) is None
    assert len(calls) == 2


async def test_concurrent_misses_reload_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page load fans out into dozens of API calls carrying the same unknown token (85 in one
    burst on the London replica). They must share one reload, and all of them must see it."""
    calls: list[int] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_load() -> int:
        calls.append(1)
        tenant_registry._last_reload = time.monotonic()
        started.set()
        await release.wait()
        tenant_registry._by_org = {"org_a": ENV_A, "org_b": ENV_B}
        return 2

    monkeypatch.setattr(tenant_registry, "load", slow_load)
    tasks = [asyncio.create_task(tenant_registry.resolve("org_b", None)) for _ in range(20)]
    await started.wait()
    release.set()
    results = await asyncio.gather(*tasks)
    assert all(r is ENV_B for r in results)
    assert len(calls) == 1


async def test_a_failing_reload_degrades_to_the_current_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database blip on the miss path must not turn token verification into a 500 — the miss is
    answered as today (None → fallback environment), and known firms keep resolving."""

    async def broken_load() -> int:
        tenant_registry._last_reload = time.monotonic()
        raise RuntimeError("db down")

    monkeypatch.setattr(tenant_registry, "load", broken_load)
    assert await tenant_registry.resolve("org_unknown", None) is None
    assert await tenant_registry.resolve("org_a", None) is ENV_A


async def test_load_stamps_the_clock_even_when_the_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The throttle is keyed on the ATTEMPT, so a dead database is retried once per interval from
    the miss path rather than on every request."""

    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("no connection")

        async def __aexit__(self, *_: object) -> None:
            return None

    import app.db as db

    monkeypatch.setattr(db, "system_transaction", lambda: _Boom())
    with pytest.raises(RuntimeError):
        await tenant_registry.load()
    assert tenant_registry._last_reload != float("-inf")
