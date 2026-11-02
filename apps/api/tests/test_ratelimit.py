"""The unauthenticated-surface guards: the rate limiter, the surfaces wired to it, and the
CORS origin-regex validation that stops a too-broad pattern handing sessions to an attacker.

Nothing here needs Docker: the limiter is in-process and the two endpoints under test either
short-circuit before touching the database (the throttled path) or don't touch it at all.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app import environments, ratelimit
from app.config import Settings, settings
from app.main import app
from app.routers import internal
from tests.logcapture import captured, messages


@pytest.fixture(autouse=True)
def _clean_limiter() -> None:
    """Counters are process-global; every test starts from zero."""
    ratelimit.limiter.reset()


class FakeClock:
    """A monotonic clock we can advance, so window rollover is tested without sleeping."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# ---- the limiter ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allows_up_to_the_limit_then_blocks() -> None:
    clock = FakeClock()
    limiter = ratelimit.FixedWindowLimiter(clock=clock)
    limit = ratelimit.Limit(3, 60)
    for _ in range(3):
        assert (await limiter.hit("login", "a@example.com", limit)).allowed
    blocked = await limiter.hit("login", "a@example.com", limit)
    assert not blocked.allowed
    assert 0 < blocked.retry_after <= 60
    assert limiter.rejections()["login"] == 1


@pytest.mark.asyncio
async def test_window_rolls_and_the_key_is_allowed_again() -> None:
    clock = FakeClock()
    limiter = ratelimit.FixedWindowLimiter(clock=clock)
    limit = ratelimit.Limit(1, 60)
    assert (await limiter.hit("login", "a@example.com", limit)).allowed
    assert not (await limiter.hit("login", "a@example.com", limit)).allowed
    clock.now += 61
    assert (await limiter.hit("login", "a@example.com", limit)).allowed


@pytest.mark.asyncio
async def test_a_flood_on_one_surface_cannot_evict_another_surfaces_counters() -> None:
    """The key space is LRU-evicted because keys are attacker-chosen. With ONE global LRU, volume on
    the (then-live) public share endpoint pushed out the LOGIN counters — so an attacker being
    throttled for password spraying could flood it with distinct tokens and walk straight out of the
    throttle. Eviction now happens within the inserting surface. Driven here on forgot-password,
    whose per-email key is attacker-varied in exactly the same way."""
    clock = FakeClock()
    limiter = ratelimit.FixedWindowLimiter(max_keys=8, clock=clock)
    login = ratelimit.Limit(1, 900)
    flood = ratelimit.Limit(1_000_000, 3600)

    # The attacker uses up their login budget.
    assert (await limiter.hit("login", "victim@example.com", login)).allowed
    assert not (await limiter.hit("login", "victim@example.com", login)).allowed

    # Then floods a different surface with far more distinct keys than the whole key space holds.
    for i in range(200):
        await limiter.hit(ratelimit.FORGOT_PASSWORD, f"email:{i}@example.com", flood)

    # The login counter must have survived: still throttled.
    assert not (await limiter.hit("login", "victim@example.com", login)).allowed


@pytest.mark.asyncio
async def test_keys_and_surfaces_are_independent() -> None:
    limiter = ratelimit.FixedWindowLimiter(clock=FakeClock())
    limit = ratelimit.Limit(1, 60)
    assert (await limiter.hit("login", "a@example.com", limit)).allowed
    assert (await limiter.hit("login", "b@example.com", limit)).allowed
    assert (await limiter.hit(ratelimit.FORGOT_PASSWORD, "a@example.com", limit)).allowed


@pytest.mark.asyncio
async def test_disabled_limit_and_global_switch_never_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = ratelimit.FixedWindowLimiter(clock=FakeClock())
    disabled = ratelimit.Limit(0, 60)
    for _ in range(5):
        assert (await limiter.hit("login", "a@example.com", disabled)).allowed
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    real = ratelimit.Limit(1, 60)
    for _ in range(5):
        assert (await limiter.hit("login", "b@example.com", real)).allowed


@pytest.mark.asyncio
async def test_key_space_is_bounded_by_lru_eviction() -> None:
    """An attacker-chosen key space must not grow without bound; eviction fails OPEN."""
    limiter = ratelimit.FixedWindowLimiter(max_keys=2, clock=FakeClock())
    limit = ratelimit.Limit(1, 60)
    await limiter.hit("login", "one", limit)
    await limiter.hit("login", "two", limit)
    await limiter.hit("login", "three", limit)  # evicts "one"
    assert (await limiter.hit("login", "one", limit)).allowed


@pytest.mark.asyncio
async def test_check_applies_every_limit_and_reports_the_longest_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 1)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 100)
    keyed = ratelimit.forgot_password_limits(
        email="A@Example.com", host="app.firm.com", client_ip="203.0.113.9"
    )
    assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)).allowed
    breached = await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)
    assert not breached.allowed
    assert breached.retry_after > 0


@pytest.mark.asyncio
async def test_a_rejected_request_consumes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE regression: counting a rejected request turns a shared budget into a DoS primitive.

    One attacker on one address is stopped by the per-email cap after N requests, but every
    further attempt used to keep counting against the per-portal-host cap — so ~60 anonymous POSTs
    locked every OTHER user of that firm out of password reset for the hour, and the endpoint
    answered {"ok": true} the whole time. Nothing may be counted unless the request is admitted.
    """
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 1)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 3)
    attacker = ratelimit.forgot_password_limits(
        email="attacker@example.com", host="app.firm.com", client_ip=None
    )
    assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, attacker)).allowed
    for _ in range(20):
        assert not (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, attacker)).allowed
    # The attacker consumed exactly ONE of the host's three: two innocent users still get through.
    for victim in ("victim@example.com", "another@example.com"):
        keyed = ratelimit.forgot_password_limits(email=victim, host="app.firm.com", client_ip=None)
        assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)).allowed, (
            f"{victim} was denied a password reset by someone else's abuse"
        )


@pytest.mark.asyncio
async def test_a_shared_budget_never_refuses_it_only_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE wave-3 regression: a shared-key budget must not be able to refuse anybody.

    The per-portal-host budget is reached by varying the email field of an unauthenticated form, so
    any *enforcing* cap on it is a firm-wide password-reset outage available to an anonymous
    caller. It is advisory: exceeded, counted, alerted — never enforced.
    """
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 2)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 4)
    for i in range(2):  # two addresses × two each == the whole host budget
        keyed = ratelimit.forgot_password_limits(
            email=f"u{i}@example.com", host="app.firm.com", client_ip=None
        )
        for _ in range(2):
            assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)).allowed
    third = ratelimit.forgot_password_limits(
        email="u2@example.com", host="app.firm.com", client_ip=None
    )
    with captured("app.ratelimit") as records:
        assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, third)).allowed, (
            "a shared budget refused a request — that is the outage, not the control"
        )
    assert ratelimit.limiter.rejections()[ratelimit.FORGOT_PASSWORD] == 0
    assert ratelimit.limiter.budget_exceeded()[ratelimit.FORGOT_PASSWORD] == 1
    assert records[-1].levelno == logging.ERROR
    assert records[-1].event == "rate_limit_budget_exceeded"
    # ...and a DIFFERENT firm's portal has its own budget: it is per host, not global.
    other_host = ratelimit.forgot_password_limits(
        email="u2@example.com", host="app.other-firm.com", client_ip=None
    )
    assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, other_host)).allowed
    assert ratelimit.limiter.budget_exceeded()[ratelimit.FORGOT_PASSWORD] == 1


@pytest.mark.asyncio
async def test_a_shared_budget_alerts_once_per_window_not_once_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An advisory budget keeps being exceeded for the rest of the window (nothing refuses), so an
    alert per request would be thousands of ERROR lines and a rate-limited error tracker — i.e. the
    alarm destroying itself. Once per key per window, then again after the window rolls."""
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 1)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 1)
    clock = FakeClock()
    monkeypatch.setattr(ratelimit.limiter, "_clock", clock)
    with captured("app.ratelimit") as records:
        for i in range(50):
            keyed = ratelimit.forgot_password_limits(
                email=f"u{i}@example.com", host="app.firm.com", client_ip=None
            )
            assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)).allowed
    errors = [r for r in records if r.levelno == logging.ERROR]
    assert len(errors) == 1, f"{len(errors)} alerts for one flood"
    # ...but every over-budget request is still COUNTED, so the metric shows the real volume.
    assert ratelimit.limiter.budget_exceeded()[ratelimit.FORGOT_PASSWORD] == 49
    clock.now += ratelimit.HOUR + 1
    with captured("app.ratelimit") as after:
        for i in range(3):
            keyed = ratelimit.forgot_password_limits(
                email=f"v{i}@example.com", host="app.firm.com", client_ip=None
            )
            await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)
    assert len([r for r in after if r.levelno == logging.ERROR]) == 1, (
        "the new window must alert again — otherwise a persistent flood goes quiet forever"
    )


@pytest.mark.asyncio
async def test_a_recent_shared_exceedance_is_visible_and_then_goes_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``recent_budget_exceeded`` is what /health/ready's `abuse` probe reads: it must light up on
    a live flood and clear itself afterwards, or the deployment sits at 'degraded' forever."""
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 1)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 1)
    clock = FakeClock()
    monkeypatch.setattr(ratelimit.limiter, "_clock", clock)
    assert ratelimit.limiter.recent_budget_exceeded() == {}
    for i in range(2):
        keyed = ratelimit.forgot_password_limits(
            email=f"u{i}@example.com", host="app.firm.com", client_ip=None
        )
        await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)
    assert ratelimit.limiter.recent_budget_exceeded() == {ratelimit.FORGOT_PASSWORD: 1}
    clock.now += ratelimit.HOUR + 1
    assert ratelimit.limiter.recent_budget_exceeded() == {}


@pytest.mark.asyncio
async def test_a_narrow_breach_warns_a_shared_one_errors_and_neither_logs_the_raw_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusing the abuser is routine (WARNING). Serving requests over a shared budget is a spend
    incident an operator has to hear about (ERROR + the error tracker) — otherwise the alert that
    matters drowns in the one that doesn't. Neither may print the email address or the host."""
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 1)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 1)
    keyed = ratelimit.forgot_password_limits(
        email="a@example.com", host="app.firm.com", client_ip=None
    )
    assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)).allowed
    with captured("app.ratelimit") as narrow:
        # Same address again: the NARROW cap breaches first, so this must not page anyone.
        assert not (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)).allowed
    assert [r.levelno for r in narrow] == [logging.WARNING]
    assert narrow[-1].shared_key is False

    # A different address, whose own cap is fresh: now the SHARED host budget is what is exceeded.
    other = ratelimit.forgot_password_limits(
        email="b@example.com", host="app.firm.com", client_ip=None
    )
    with captured("app.ratelimit") as shared:
        assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, other)).allowed
    assert shared[-1].levelno == logging.ERROR
    assert shared[-1].shared_key is True
    assert "shared rate-limit budget exceeded" in shared[-1].getMessage()
    # The raw key is a portal host / an email address — only ever a hash reaches the log.
    rendered = " ".join(messages(narrow + shared) + [str(r.__dict__) for r in narrow + shared])
    assert "app.firm.com" not in rendered
    assert "b@example.com" not in rendered


@pytest.mark.asyncio
async def test_the_spend_alarm_cannot_be_evicted_by_flooding_distinct_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trying to get ROUND the alarm now that nothing refuses: the key space is LRU-evicted and
    eviction fails open, so if a flood of distinct addresses could push the host counter out, the
    budget would reset and an operator would never be told. It cannot — every request the attacker
    makes also touches (and so refreshes) the host key, which keeps it newest.

    A tiny `max_keys` here is the same shape as 500 junk addresses against the shipped 50,000.
    """
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 1)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 2)
    limiter = ratelimit.FixedWindowLimiter(max_keys=3, clock=FakeClock())
    for i in range(60):
        keyed = ratelimit.forgot_password_limits(
            email=f"junk-{i}@example.invalid", host="app.firm.com", client_ip=None
        )
        assert (await limiter.check(ratelimit.FORGOT_PASSWORD, keyed)).allowed
    assert limiter.budget_exceeded()[ratelimit.FORGOT_PASSWORD] == 58, (
        "the host counter was evicted, so the spend alarm went quiet under a bigger flood"
    )
    assert limiter.recent_budget_exceeded()[ratelimit.FORGOT_PASSWORD] == 58


@pytest.mark.asyncio
async def test_one_rejection_is_counted_per_request_not_per_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`graphene_rate_limit_rejected_total` counts refusals a caller experienced; counting each
    breached limit separately inflated it by the number of keys on the surface."""
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 1)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 1)
    keyed = ratelimit.forgot_password_limits(
        email="a@example.com", host="app.firm.com", client_ip="203.0.113.9"
    )
    assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)).allowed
    # This request breaches the per-email AND the per-host limit at once: one refusal, one count.
    assert not (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)).allowed
    assert ratelimit.limiter.rejections()[ratelimit.FORGOT_PASSWORD] == 1


@pytest.mark.asyncio
async def test_a_throttled_key_cannot_be_evicted_out_of_its_own_throttle() -> None:
    """Eviction fails OPEN, so a rejected key must keep its LRU position: otherwise an attacker
    walks out of the throttle by hammering unrelated keys until their own counter is dropped."""
    limiter = ratelimit.FixedWindowLimiter(max_keys=3, clock=FakeClock())
    limit = ratelimit.Limit(1, 60)
    assert (await limiter.hit("login", "attacker", limit)).allowed
    for i in range(10):
        await limiter.hit("login", f"noise-{i}", limit)
        assert not (await limiter.hit("login", "attacker", limit)).allowed


@pytest.mark.asyncio
async def test_check_reports_the_tightest_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 3)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 100)
    keyed = ratelimit.forgot_password_limits(
        email="a@example.com", host="app.firm.com", client_ip=None
    )
    decision = await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, keyed)
    assert (decision.limit, decision.remaining) == (3, 2)


@pytest.mark.asyncio
async def test_email_keys_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """`A@X.com` and `a@x.com` are the same inbox — the cap must not be per-spelling."""
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 1)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 0)
    first = ratelimit.forgot_password_limits(email="A@X.com", host="h", client_ip=None)
    second = ratelimit.forgot_password_limits(email="a@x.com ", host="h", client_ip=None)
    assert (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, first)).allowed
    assert not (await ratelimit.limiter.check(ratelimit.FORGOT_PASSWORD, second)).allowed


@pytest.mark.asyncio
async def test_enforce_raises_a_429_carrying_retry_after() -> None:
    """``enforce`` is the shape a route dependency uses (``app/routers/email_templates.py``): refuse
    with a status a client can act on, and a ``Retry-After`` it can wait for."""
    limit = ratelimit.Limit(1, ratelimit.HOUR)
    await ratelimit.enforce(ratelimit.EMAIL_TEST, {"user:abc": limit}, detail="nope")
    with pytest.raises(HTTPException) as exc:
        await ratelimit.enforce(ratelimit.EMAIL_TEST, {"user:abc": limit}, detail="nope")
    assert exc.value.status_code == 429
    assert int((exc.value.headers or {})["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_enforce_never_raises_on_a_shared_key_alone() -> None:
    """The one thing ``enforce`` must not do: turn an advisory budget into a refusal. Every caller
    on a shared key is a third party — the per-firm email-test budget is shared by every author at
    the firm, so refusing on it would stop a colleague testing their own template."""
    advisory = ratelimit.Limit(1, ratelimit.HOUR, shared=True)
    for _ in range(20):
        await ratelimit.enforce(ratelimit.EMAIL_TEST, {"tenant:7": advisory}, detail="nope")
    assert ratelimit.limiter.rejections()[ratelimit.EMAIL_TEST] == 0
    assert ratelimit.limiter.budget_exceeded()[ratelimit.EMAIL_TEST] == 19


def test_the_surfaces_tuple_is_what_the_counters_are_keyed_over() -> None:
    """``rejections()``/``budget_exceeded()`` build their dict from ``SURFACES``, so that tuple is
    the ``/metrics`` label set, not documentation. It carried a ``share`` surface for the document
    share-links feature and lost it with the feature — hence a series disappearing from
    ``graphene_rate_limit_*`` is expected there and a regression anywhere else.
    """
    assert set(ratelimit.limiter.rejections()) == set(ratelimit.SURFACES)
    assert set(ratelimit.limiter.budget_exceeded()) == set(ratelimit.SURFACES)
    assert "share" not in ratelimit.SURFACES


# ---- POST /internal/rate-limit (the portals' login form) --------------------------------------


@pytest.fixture
def internal_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "internal_api_token", "test-internal-token")
    return "test-internal-token"


@pytest.mark.asyncio
async def test_rate_limit_endpoint_reports_the_breach(
    internal_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_login_per_15m", 2)
    monkeypatch.setattr(settings, "rate_limit_login_ip_per_15m", 0)
    body = {
        "surface": "login",
        "identifier": "staff@firm.com",
        "host": "platform.firm.com",
        "client_ip": "203.0.113.4",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"x-internal-token": internal_token}
        first = await client.post("/internal/rate-limit", json=body, headers=headers)
        second = await client.post("/internal/rate-limit", json=body, headers=headers)
        third = await client.post("/internal/rate-limit", json=body, headers=headers)
    assert first.json() == {"allowed": True, "retry_after_seconds": 0}
    assert second.json()["allowed"] is True
    # 200, not 429: the portal needs to render its own "too many attempts" copy.
    assert third.status_code == 200
    assert third.json()["allowed"] is False
    assert third.json()["retry_after_seconds"] > 0


@pytest.mark.asyncio
async def test_mfa_surface_caps_guesses_per_challenge_and_per_ip(
    internal_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A six-digit TOTP against a minutes-long pending token is a brute force unless each challenge
    is worth only a handful of guesses. The identifier is the portal's opaque handle for the
    challenge — never the pending token itself."""
    monkeypatch.setattr(settings, "rate_limit_mfa_per_challenge", 2)
    monkeypatch.setattr(settings, "rate_limit_mfa_ip_per_15m", 3)
    headers = {"x-internal-token": internal_token}
    one = {
        "surface": "mfa",
        "identifier": "chal-1",
        "host": "app.firm.com",
        "client_ip": "203.0.113.4",
    }
    two = {**one, "identifier": "chal-2"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:

        async def allowed(body: dict[str, str]) -> bool:
            resp = await client.post("/internal/rate-limit", json=body, headers=headers)
            return bool(resp.json()["allowed"])

        assert await allowed(one)
        assert await allowed(one)
        # Third guess at the same challenge is refused; a fresh challenge from the same IP still
        # has one IP token left, and after that the IP cap bites across challenges.
        assert not await allowed(one)
        assert await allowed(two)
        assert not await allowed(two)


@pytest.mark.asyncio
async def test_rate_limit_endpoint_requires_the_internal_token(internal_token: str) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/rate-limit",
            json={"surface": "login", "identifier": "a@b.com", "host": "h"},
            headers={"x-internal-token": "wrong"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rate_limit_endpoint_rejects_an_unknown_surface(internal_token: str) -> None:
    """Buckets are enumerated — a caller must not be able to invent an unmetered one."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/rate-limit",
            json={"surface": "anything", "identifier": "a@b.com", "host": "h"},
            headers={"x-internal-token": internal_token},
        )
    assert resp.status_code == 422


# ---- POST /internal/forgot-password ----------------------------------------------------------


@pytest.mark.asyncio
async def test_forgot_password_stops_calling_workos_once_throttled(
    internal_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unauthenticated reset form is an email bomb + a way to burn our Resend quota. Past the
    cap it must send nothing — while returning the same {ok: true} (no account-existence oracle,
    and no "you are throttled" oracle either)."""
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 2)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 0)
    calls: list[str] = []

    class _StubWorkOS:
        class user_management:  # noqa: N801 - mirrors the SDK's attribute name
            @staticmethod
            def reset_password(*, email: str) -> Any:
                calls.append(email)
                raise LookupError("boom")  # exercised by the outage-classification test below

    monkeypatch.setattr(environments, "env_config_by_host", _none_config)
    monkeypatch.setattr(environments, "core_workos_client", lambda: _StubWorkOS())
    app.dependency_overrides[internal.internal_session] = _no_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(4):
                resp = await client.post(
                    "/internal/forgot-password",
                    json={"email": "victim@example.com", "host": "app.firm.com"},
                    headers={"x-internal-token": internal_token},
                )
                assert resp.status_code == 200
                assert resp.json() == {"ok": True}
    finally:
        app.dependency_overrides.pop(internal.internal_session, None)
    assert len(calls) == 2, "WorkOS was called past the throttle"


@pytest.mark.asyncio
async def test_forgot_password_abuse_of_one_address_does_not_lock_out_the_firm(
    internal_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-to-end shape of the shared-budget bug, on the endpoint the portals actually call.

    An anonymous attacker hammers ONE address past its cap. Every other user of that portal must
    still be able to reset their password — before the fix the attacker's rejected attempts kept
    draining the per-host budget, and the victim's request silently sent no email at all.
    """
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 2)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 3)
    calls: list[str] = []

    class _StubWorkOS:
        class user_management:  # noqa: N801 - mirrors the SDK's attribute name
            @staticmethod
            def reset_password(*, email: str) -> Any:
                calls.append(email)
                raise LookupError("no such user")

    monkeypatch.setattr(environments, "env_config_by_host", _none_config)
    monkeypatch.setattr(environments, "core_workos_client", lambda: _StubWorkOS())
    app.dependency_overrides[internal.internal_session] = _no_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"x-internal-token": internal_token}
            for _ in range(10):
                await client.post(
                    "/internal/forgot-password",
                    json={"email": "attacker-target@example.com", "host": "app.firm.com"},
                    headers=headers,
                )
            resp = await client.post(
                "/internal/forgot-password",
                json={"email": "real-user@example.com", "host": "app.firm.com"},
                headers=headers,
            )
    finally:
        app.dependency_overrides.pop(internal.internal_session, None)
    assert resp.status_code == 200
    assert calls.count("attacker-target@example.com") == 2, "the per-email cap did not bite"
    assert "real-user@example.com" in calls, (
        "an innocent user's password reset was swallowed by someone else's throttle"
    )


@pytest.mark.asyncio
async def test_500_distinct_addresses_cannot_lock_a_firm_out_of_password_reset(
    internal_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE headline harm, driven at PRODUCTION DEFAULTS through the real ASGI app.

    Wave 2's verifier put 500 requests with 500 made-up addresses through one portal host: each was
    admitted (its own per-email budget was fresh), each consumed one of the shared 500/hour host
    budget, and the NEXT request — an innocent real user — was refused, returned ``{"ok": true}``
    and never reached WorkOS. An anonymous caller had turned an abuse control into a firm-wide
    password-reset outage; making the counting honest only raised the price from ~60 POSTs to 500.

    The fix is that a shared-key budget cannot refuse at all. So: run the attack, then assert the
    innocent user is served, that nobody was rejected, and that the operator was told.
    """
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 5)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 500)
    calls: list[str] = []

    class _StubWorkOS:
        class user_management:  # noqa: N801 - mirrors the SDK's attribute name
            @staticmethod
            def reset_password(*, email: str) -> Any:
                calls.append(email)
                # 404-shaped: the ordinary "no such user" answer. Reaching HERE is the assertion —
                # it means the request was not swallowed by somebody else's throttle.
                raise _WorkOSMiss()

    monkeypatch.setattr(environments, "env_config_by_host", _none_config)
    monkeypatch.setattr(environments, "core_workos_client", lambda: _StubWorkOS())
    app.dependency_overrides[internal.internal_session] = _no_session
    try:
        transport = ASGITransport(app=app)
        with captured("app.ratelimit") as records:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                headers = {"x-internal-token": internal_token}
                for i in range(500):
                    resp = await client.post(
                        "/internal/forgot-password",
                        json={"email": f"junk-{i}@example.invalid", "host": "app.firm.com"},
                        headers=headers,
                    )
                    assert resp.status_code == 200
                innocent = await client.post(
                    "/internal/forgot-password",
                    json={"email": "real-user@firm.com", "host": "app.firm.com"},
                    headers=headers,
                )
    finally:
        app.dependency_overrides.pop(internal.internal_session, None)

    assert innocent.status_code == 200
    assert "real-user@firm.com" in calls, (
        "500 anonymous POSTs with made-up addresses locked a real user out of password reset"
    )
    assert ratelimit.limiter.rejections()[ratelimit.FORGOT_PASSWORD] == 0
    # The spend alarm still fires — once — and names no host or address.
    errors = [r for r in records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].event == "rate_limit_budget_exceeded"
    assert ratelimit.limiter.budget_exceeded()[ratelimit.FORGOT_PASSWORD] > 0
    assert "firm.com" not in " ".join(messages(errors) + [str(r.__dict__) for r in errors])


@pytest.mark.asyncio
async def test_the_per_address_cap_survives_case_and_whitespace_variation(
    internal_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrow per-address cap is now the ONLY thing that refuses on this surface, so try to get
    round it the cheap ways: re-spell the same inbox. `Victim@…`/` victim@… ` must share one bucket.

    (Plus-addressing and gmail dots produce a different key on purpose — they are a different
    identity to WorkOS, so the reset mail is never sent to the victim's inbox at all.)
    """
    monkeypatch.setattr(settings, "rate_limit_forgot_password_per_hour", 2)
    monkeypatch.setattr(settings, "rate_limit_forgot_password_host_per_hour", 500)
    calls: list[str] = []

    class _StubWorkOS:
        class user_management:  # noqa: N801 - mirrors the SDK's attribute name
            @staticmethod
            def reset_password(*, email: str) -> Any:
                calls.append(email)
                raise _WorkOSMiss()

    monkeypatch.setattr(environments, "env_config_by_host", _none_config)
    monkeypatch.setattr(environments, "core_workos_client", lambda: _StubWorkOS())
    app.dependency_overrides[internal.internal_session] = _no_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for spelling in (
                "victim@firm.com",
                "VICTIM@Firm.com",
                " victim@firm.com ",
                "Victim@FIRM.com",
            ):
                await client.post(
                    "/internal/forgot-password",
                    json={"email": spelling, "host": "app.firm.com"},
                    headers={"x-internal-token": internal_token},
                )
    finally:
        app.dependency_overrides.pop(internal.internal_session, None)
    assert len(calls) == 2, f"the per-address cap was beaten by re-spelling it: {calls}"


class _WorkOSMiss(Exception):
    """A 404-shaped WorkOS error — the ordinary "no such user" answer."""

    status_code = 404


async def _none_config(session: object, host: str) -> None:
    return None


async def _no_session() -> Any:
    """The throttled path returns before the session is used; the non-throttled path here only
    reaches the stubbed WorkOS client."""
    yield None


def test_expected_workos_miss_is_classified_by_status_code() -> None:
    """An unknown email (4xx) is the ordinary privacy-preserving no-op; a 5xx, a 429 or a
    connection error means every reset on the instance is failing and must be reported."""

    class _Err(Exception):
        def __init__(self, code: int | None) -> None:
            self.status_code = code

    assert internal._is_expected_workos_miss(_Err(404))
    assert internal._is_expected_workos_miss(_Err(400))
    assert not internal._is_expected_workos_miss(_Err(429))
    assert not internal._is_expected_workos_miss(_Err(500))
    assert not internal._is_expected_workos_miss(_Err(None))
    assert not internal._is_expected_workos_miss(TimeoutError("connect"))


# ---- ALLOWED_ORIGIN_REGEX --------------------------------------------------------------------
# The other guard on the unauthenticated edge: paired with `allow_credentials=True`, a pattern
# that matches an attacker origin is a cross-origin session-theft primitive.


def test_origin_regex_is_anchored() -> None:
    parsed = Settings(_env_file=None, allowed_origin_regex=r"https://[a-z]+\.graphene\.app")
    assert parsed.allowed_origin_regex == r"^(?:https://[a-z]+\.graphene\.app)$"


def test_already_anchored_regex_is_left_alone() -> None:
    pattern = r"^https://(client|platform)\.graphene\.app$"
    assert Settings(_env_file=None, allowed_origin_regex=pattern).allowed_origin_regex == pattern


def test_blank_origin_regex_stays_blank() -> None:
    assert Settings(_env_file=None, allowed_origin_regex="   ").allowed_origin_regex == ""


@pytest.mark.parametrize(
    "pattern",
    [
        ".*",
        "^https://.+$",
        r"^https://.*\.example$|.*",
        "^http://.*$",
    ],
)
def test_too_broad_origin_regex_fails_startup(pattern: str) -> None:
    with pytest.raises(ValueError, match="too broad"):
        Settings(_env_file=None, allowed_origin_regex=pattern)


def test_invalid_origin_regex_fails_startup() -> None:
    with pytest.raises(ValueError, match="not a valid regex"):
        Settings(_env_file=None, allowed_origin_regex="^https://(unclosed$")
