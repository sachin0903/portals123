"""Rate limiting for the UNAUTHENTICATED surfaces (login, forgot-password).

Everything reachable without a session is free to hammer: password spraying against WorkOS, email
bombing through our own Resend account and quota. This module is the one place those caps live.

**One authenticated surface is capped too** (``email-test``: the email-template author mailing a
test to their OWN address). Authentication is not the point of a cap here — the harm is the SPEND,
which is the same Resend quota the forgot-password cap protects, and a held-down button in an editor
costs exactly as much as an anonymous flood. It lives here rather than growing a second throttle
implementation, which is the whole reason this module is "the one place those caps live".

**Shape.** A fixed-window counter per ``(surface, key)``: ``N`` requests per window, then a 429
carrying ``Retry-After``. Fixed (not sliding) windows on purpose — one integer per key, no
per-request storage growth, and the failure mode (a burst straddling a window boundary can reach
2N) is irrelevant for abuse control at these magnitudes.

**Scope.** The store is IN-PROCESS. With the current single-container deploy that is the real
limit; with N API replicas the effective cap is N×limit, which still bounds abuse by a constant.
A shared (Postgres/Redis) store slots in behind ``FixedWindowLimiter`` without changing any call
site — see the handoff note in the audit fix. It replaces the never-wired
``flows.public_start_throttle`` table + ``public_onboarding_max_per_hour`` setting, which were
built for an endpoint that does not exist.

**Keys.** The API sits behind each portal's server-side fetch, so ``request.client.host`` is the
portal container, not the end user — the portal must pass the end user's IP explicitly
(``client_ip``) for an IP-keyed limit to mean anything. Where it doesn't, the identity-shaped key
(email, portal host) still applies, and that is the one that bounds the expensive side effect. An
address-shaped key that CAN collapse to one bucket for everybody must be ``shared`` (advisory) for
the same reason a portal-host key must be.

**Who owns which surface's keys.** ``login_limits`` and ``forgot_password_limits`` live here (both
are consumed by ``app/routers/internal.py``); ``email_test_limits`` is consumed by
``app/routers/email_templates.py``.

**A shared budget NEVER refuses (``Limit.shared``).** A key like ``host:<portal host>`` is shared
by every user of that portal, so refusing on it refuses *third parties*, not the abuser — and the
key an anonymous caller varies to reach it (the email address in the request body) costs them
nothing. Any cap on such a key is therefore a denial-of-service primitive aimed at us: 500 POSTs
with 500 made-up addresses used to exhaust the per-host forgot-password budget and lock every real
user of that firm out of password reset for the hour, while the endpoint kept answering
``{"ok": true}``. Making the *counting* honest (rule 1 below) only raised the price of the outage.

So a shared limit is ADVISORY: it is counted, and crossing it is reported once per window through
``report_error`` (ERROR + the error tracker + ``graphene_rate_limit_budget_exceeded_total``), but
it never turns a request away. What bounds the abuse is the NARROW keys, which identify the caller:

1. ``check()`` counts NOTHING unless every *enforcing* (narrow) limit admits the request, so a
   caller already rejected by the per-email limit cannot burn the shared budget either.
2. Only narrow limits refuse. A shared one degrades to an alert, because "refuse everyone on this
   key" is a worse outcome than the spend it was protecting.

The residual bound on an anonymous flood of *distinct* addresses is the per-end-user-IP limit,
which needs the portal to pass ``client_ip`` (SvelteKit ``getClientAddress()``); until it does,
that surface is capped per address only. See the handoff in the audit fix.

**Observability.** Every rejected request is counted once per surface (not once per breached
limit) and logged once at WARNING, carrying the surface and a HASHED key, never the raw email, IP
or host. Rejections surface in ``/metrics`` as ``graphene_rate_limit_rejected_total``, advisory
exceedances as ``graphene_rate_limit_budget_exceeded_total``, and a *recent* advisory exceedance
also degrades ``/health/ready``'s ``abuse`` probe — the one operator surface that needs no token.
A throttle nobody can see is a throttle nobody trusts.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

from fastapi import HTTPException, status

from app.config import settings
from app.logging import report_error

logger = logging.getLogger(__name__)

# The surfaces we cap. Stable strings — they are the metric label and the internal API's contract.
# A ``share`` surface stood here for the document share-links feature. It went with the feature, so
# ``/metrics`` no longer carries a ``surface="share"`` series on either rate-limit counter — a
# deliberate shape change, not a regression. Nothing else keys off this tuple's membership.
LOGIN = "login"
FORGOT_PASSWORD = "forgot-password"  # noqa: S105 — a surface name, not a credential
MFA = "mfa"  # a TOTP code offered against one pending WorkOS challenge
EMAIL_TEST = "email-test"  # authenticated: an author mailing an email template to their own address
POSTCODER = "postcoder"  # authenticated: a paid third-party address lookup, per keystroke
SEARCH = "search"  # authenticated: the portals' ⌘K search, a multi-table query per keystroke
SURFACES: tuple[str, ...] = (LOGIN, FORGOT_PASSWORD, MFA, EMAIL_TEST, POSTCODER, SEARCH)

MINUTE = 60
HOUR = 3600


@dataclass(frozen=True, slots=True)
class Limit:
    """``max_requests`` per ``window_seconds`` for one key. ``max_requests <= 0`` disables it."""

    max_requests: int
    window_seconds: int
    # True when the key is shared by many users (a portal host) rather than identifying the
    # caller. Such a limit is ADVISORY: it is counted and crossing it alerts, but it never
    # refuses a request — refusing on a shared key refuses innocent third parties, which is the
    # outage the abuse control was supposed to prevent. See the module docstring.
    shared: bool = False

    @property
    def disabled(self) -> bool:
        return self.max_requests <= 0 or self.window_seconds <= 0


class Decision(NamedTuple):
    """A check's outcome. ``retry_after`` is seconds until the window rolls (0 when allowed)."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int


_ALLOWED = Decision(allowed=True, limit=0, remaining=-1, retry_after=0)


class FixedWindowLimiter:
    """In-process fixed-window counters with a bounded, LRU-evicted key space.

    Bounded because the keys are attacker-chosen (any email, any IP): without a cap this would be
    a memory-exhaustion vector of its own. Eviction under pressure loses a counter, i.e. fails
    OPEN for that key — deliberate: an abuse control must not become an availability control.
    """

    def __init__(self, *, max_keys: int = 50_000, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._max_keys = max_keys
        self._windows: OrderedDict[tuple[str, str], tuple[int, int]] = OrderedDict()
        self._rejections: Counter[str] = Counter()
        # Advisory (shared-key) budgets crossed: the count per surface, and the window in which
        # each key last alerted. An advisory limit does not refuse, so it keeps being crossed for
        # the rest of the window — alerting once per (surface, key, window) is what stops one
        # flood becoming thousands of ERROR lines and a rate-limited error tracker.
        self._budget_exceeded: Counter[str] = Counter()
        self._alerted: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._last_exceeded: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _peek(self, surface: str, key: str, limit: Limit) -> Decision:
        """Would one more request against ``(surface, key)`` be allowed? Counts NOTHING.

        Caller must hold ``self._lock``. The key's LRU position IS refreshed even on a rejection:
        without that, an attacker being throttled could evict their own counter by hammering other
        keys and walk straight out of the throttle.
        """
        if limit.disabled:
            return _ALLOWED
        now = self._clock()
        window = int(now // limit.window_seconds)
        retry_after = int(limit.window_seconds - (now % limit.window_seconds)) or 1
        cache_key = (surface, key)
        current = self._windows.get(cache_key)
        used = current[1] if current and current[0] == window else 0
        if cache_key in self._windows:
            self._windows.move_to_end(cache_key)
        if used + 1 > limit.max_requests:
            return Decision(False, limit.max_requests, 0, retry_after)
        return Decision(True, limit.max_requests, limit.max_requests - (used + 1), 0)

    def _bump(self, surface: str, key: str, limit: Limit) -> None:
        """Count one admitted request. Caller must hold ``self._lock``."""
        if limit.disabled:
            return
        now = self._clock()
        window = int(now // limit.window_seconds)
        cache_key = (surface, key)
        current = self._windows.get(cache_key)
        count = current[1] + 1 if current and current[0] == window else 1
        self._windows[cache_key] = (window, count)
        self._windows.move_to_end(cache_key)
        while len(self._windows) > self._max_keys:
            # Evict within the INSERTING surface first. A single global LRU meant volume on one
            # surface evicted another's counters: flooding the (then-live) public share endpoint
            # with distinct tokens pushed out the LOGIN counters and walked an attacker straight out
            # of the password-spraying throttle. Keys are still attacker-chosen on every surface, so
            # the rule outlives the endpoint that exposed it. Iteration order is LRU, so the first
            # match is the oldest
            # key of this surface. The fallback keeps the memory bound absolute when the inserting
            # surface holds nothing else to give up.
            for held in self._windows:
                if held[0] == surface:
                    del self._windows[held]
                    break
            else:  # pragma: no cover - only when this surface has exactly one key
                self._windows.popitem(last=False)

    def _mark_alerted(self, surface: str, key: str, limit: Limit) -> bool:
        """True the FIRST time ``(surface, key)`` crosses its advisory budget in a window.

        Caller must hold ``self._lock``. An advisory limit admits every request, so without this
        one flood would emit one ERROR (and one error-tracker event) per request for the rest of
        the hour — the alert that matters drowned in its own repetition.
        """
        now = self._clock()
        window = int(now // limit.window_seconds)
        cache_key = (surface, key)
        if self._alerted.get(cache_key) == window:
            self._alerted.move_to_end(cache_key)
            return False
        self._alerted[cache_key] = window
        self._alerted.move_to_end(cache_key)
        while len(self._alerted) > self._max_keys:
            self._alerted.popitem(last=False)
        return True

    def _fields(self, surface: str, key: str, limit: Limit, decision: Decision) -> dict[str, Any]:
        return {
            "event": "rate_limit_budget_exceeded" if limit.shared else "rate_limit_exceeded",
            "surface": surface,
            # Hashed: the raw key is an email address, an end-user IP or a portal host.
            "key_hash": hashlib.sha256(key.encode()).hexdigest()[:12],
            "limit": limit.max_requests,
            "window_seconds": limit.window_seconds,
            "retry_after": decision.retry_after,
            "shared_key": limit.shared,
        }

    def _report_rejection(self, surface: str, key: str, limit: Limit, decision: Decision) -> None:
        """A NARROW limit refused this caller. Routine abuse control — WARNING, not an incident."""
        logger.warning("rate limit exceeded", extra=self._fields(surface, key, limit, decision))

    def _report_budget(self, surface: str, key: str, limit: Limit, decision: Decision) -> None:
        """An ADVISORY (shared-key) budget was crossed. Nobody was refused — that is the whole
        point — but the spend this budget sizes is now being exceeded, which an operator has to
        hear about. ERROR + the error tracker, once per key per window."""
        report_error(
            f"shared rate-limit budget exceeded on {surface} — requests are still being served "
            "(refusing on a key shared by every user of a portal would deny innocent third "
            "parties), but the spend this budget sizes is being exceeded. Find what is consuming "
            "it, or raise the cap.",
            log=logger,
            **self._fields(surface, key, limit, decision),
        )

    async def hit(self, surface: str, key: str, limit: Limit) -> Decision:
        """Count one request against ``(surface, key)`` and say whether it is allowed."""
        return await self.check(surface, {key: limit})

    async def check(self, surface: str, keyed_limits: Mapping[str, Limit]) -> Decision:
        """Apply several limits to one request (e.g. per-email AND per-host AND per-IP).

        **Only NARROW limits refuse.** A ``shared=True`` limit (a key held in common by every user
        of a portal) is advisory: crossing it alerts and is counted, but the request is still
        served. Refusing on such a key hands an anonymous caller a firm-wide outage for the price
        of varying the email address in the request body — the exact harm this control exists to
        prevent (module docstring, "A shared budget NEVER refuses").

        **Nothing is counted unless the narrow limits admit the request.** Counting a rejected
        request would let a caller already stopped by the per-email cap keep draining the shared
        budget, so the advisory alert would fire on abuse that never cost us anything.

        When several narrow limits breach, the LONGEST wait is returned: telling a caller to retry
        while another window is still closed would just earn them a second rejection.
        """
        if not settings.rate_limit_enabled:
            return _ALLOWED
        # Narrow keys first (False sorts before True), so a shared budget is only ever consulted
        # for a request the caller-specific limits have already admitted.
        ordered = sorted(keyed_limits.items(), key=lambda item: item[1].shared)
        breach: tuple[str, Limit, Decision] | None = None
        alerts: list[tuple[str, Limit, Decision]] = []
        async with self._lock:
            allowed_peeks: list[Decision] = []
            over_budget: list[tuple[str, Limit, Decision]] = []
            for key, limit in ordered:
                decision = self._peek(surface, key, limit)
                if decision.allowed:
                    allowed_peeks.append(decision)
                elif limit.shared:
                    # Advisory: does NOT refuse, and does not stop the request being counted.
                    over_budget.append((key, limit, decision))
                elif breach is None or decision.retry_after > breach[2].retry_after:
                    breach = (key, limit, decision)
            if breach is None:
                for key, limit in ordered:
                    self._bump(surface, key, limit)
                for key, limit, decision in over_budget:
                    self._budget_exceeded[surface] += 1
                    if self._mark_alerted(surface, key, limit):
                        self._last_exceeded[surface] = self._clock()
                        alerts.append((key, limit, decision))
            else:
                # One rejection per REQUEST, not one per breached limit — the metric counts
                # refusals a caller experienced.
                self._rejections[surface] += 1
        for alert in alerts:
            self._report_budget(surface, *alert)
        if breach is not None:
            self._report_rejection(surface, *breach)
            return breach[2]
        active = [d for d in allowed_peeks if d.limit > 0]
        if not active:
            return _ALLOWED
        # Report the tightest remaining budget: it is the one that will bite next.
        tightest = min(active, key=lambda d: d.remaining)
        return Decision(True, tightest.limit, tightest.remaining, 0)

    def rejections(self) -> dict[str, int]:
        """Rejections per surface since process start (for the /metrics exposition)."""
        return {surface: self._rejections.get(surface, 0) for surface in SURFACES}

    def budget_exceeded(self) -> dict[str, int]:
        """Requests served OVER an advisory (shared-key) budget, per surface, since start."""
        return {surface: self._budget_exceeded.get(surface, 0) for surface in SURFACES}

    def recent_budget_exceeded(self, *, within_seconds: float = HOUR) -> dict[str, int]:
        """Surfaces whose advisory budget was crossed within ``within_seconds`` → total count.

        The signal ``/health/ready`` reports: unlike the monotonic counter, this goes quiet again
        once the abuse stops, so an operator surface can degrade on it without degrading forever.
        """
        now = self._clock()
        return {
            surface: self._budget_exceeded.get(surface, 0)
            for surface, when in self._last_exceeded.items()
            if now - when <= within_seconds
        }

    def reset(self) -> None:
        """Drop all counters (tests, and a break-glass clear)."""
        self._windows.clear()
        self._rejections.clear()
        self._budget_exceeded.clear()
        self._alerted.clear()
        self._last_exceeded.clear()


limiter = FixedWindowLimiter()


# ---- the configured limits ------------------------------------------------------------------


def login_limits(*, email: str, host: str, client_ip: str | None) -> dict[str, Limit]:
    """Per email-on-host (spraying one account) and per end-user IP (spraying many accounts)."""
    keyed = {
        f"email:{host}:{email.strip().lower()}": Limit(
            settings.rate_limit_login_per_15m, 15 * MINUTE
        )
    }
    if client_ip:
        keyed[f"ip:{client_ip}"] = Limit(settings.rate_limit_login_ip_per_15m, 15 * MINUTE)
    return keyed


def mfa_limits(*, challenge: str, client_ip: str | None) -> dict[str, Limit]:
    """Per pending challenge (guessing one person's six digits) and per end-user IP (guessing
    across many sign-ins).

    ``challenge`` is an OPAQUE handle the portal derives from the pending authentication token —
    a hash, never the token itself, which is a credential and must not become a cache key. A
    six-digit TOTP has a million values and WorkOS's pending token lives for minutes, so an
    unthrottled verify form is a brute force with a real success rate; the per-challenge cap makes
    a challenge worth at most ``rate_limit_mfa_per_challenge`` guesses before the person has to
    sign in again and mint a new one.
    """
    keyed = {
        f"challenge:{challenge}": Limit(settings.rate_limit_mfa_per_challenge, 15 * MINUTE),
    }
    if client_ip:
        keyed[f"ip:{client_ip}"] = Limit(settings.rate_limit_mfa_ip_per_15m, 15 * MINUTE)
    return keyed


def forgot_password_limits(*, email: str, host: str, client_ip: str | None) -> dict[str, Limit]:
    """Per email (bombing one inbox — the actual control), per end-user IP, and a per-portal-host
    spend ALARM.

    The host key is shared by every user of that portal, so it is ``shared=True`` and therefore
    advisory: crossing it alerts, it never refuses. It has to be. The only thing an anonymous
    caller needs to vary to reach it is the ``email`` field of an unauthenticated form, so a
    refusing host cap is a firm-wide password-reset outage available for the price of N POSTs —
    which is what the wave-2 verifier demonstrated with 500 made-up addresses. What actually
    bounds the harm is narrow: the per-email cap (one inbox cannot be bombed) and, once the portal
    passes it, the per-end-user-IP cap (one source cannot flood many addresses).
    """
    keyed = {
        f"email:{email.strip().lower()}": Limit(settings.rate_limit_forgot_password_per_hour, HOUR)
    }
    if client_ip:
        keyed[f"ip:{client_ip}"] = Limit(settings.rate_limit_forgot_password_per_hour * 4, HOUR)
    # Last, and marked shared: evaluated only after the narrow keys have admitted the request, and
    # never a reason to refuse one.
    keyed[f"host:{host.strip().lower()}"] = Limit(
        settings.rate_limit_forgot_password_host_per_hour, HOUR, shared=True
    )
    return keyed


def email_test_limits(*, user_id: str, tenant_id: int | None) -> dict[str, Limit]:
    """Per acting USER (the narrow, enforcing key) and a per-firm spend ALARM.

    The user key is the whole control: the endpoint mails the caller's own address and nobody
    else's, so what is being bounded is our Resend spend and the caller's own inbox — not access to
    anything. Keying it on the acting user rather than the recipient address is deliberate: the
    address is resolved server-side and a caller cannot vary it, so the two are the same key, and
    the user id is the one that stays meaningful if a person's address changes mid-window.

    The tenant key is shared by every author at the firm, so — by this module's rule — it is
    ``shared=True`` and therefore advisory: it alerts, it never refuses. Refusing on it would let
    one enthusiastic author stop their colleagues from testing their own templates, which is a
    worse outcome than the spend it was protecting.
    """
    keyed = {f"user:{user_id}": Limit(settings.rate_limit_email_test_per_hour, HOUR)}
    if tenant_id is not None:
        keyed[f"tenant:{tenant_id}"] = Limit(
            settings.rate_limit_email_test_tenant_per_hour, HOUR, shared=True
        )
    return keyed


# A ``share`` surface's limits stood here (then moved next to the public share endpoints, then went
# with them when document share links were removed). Two lessons from it are worth keeping, because
# they apply to the NEXT unauthenticated surface anyone adds here:
#   * an ``ip:`` key COLLAPSES behind the portals' server-side fetch — every visitor arrives on the
#     portal container's address, so one popular link 429'd the whole platform. An address key that
#     can collapse must be ``shared`` (advisory), per the rule in the module docstring;
#   * ``headers.get("x-forwarded-for")`` returns the FIRST of a repeated header, which a caller on
#     a public endpoint can simply add — so such a key rotates per request and never bites.
#     Read the LAST hop of the LAST header.


# ---- request helpers ------------------------------------------------------------------------


def too_many_requests(decision: Decision, detail: str) -> HTTPException:
    """The 429 for a breached limit, with ``Retry-After`` so a client can behave."""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers={"Retry-After": str(decision.retry_after)},
    )


def per_user_limits(surface: str, *, user_id: str, per_minute: int) -> dict[str, Limit]:
    """One narrow, enforcing key per acting user for an AUTHENTICATED read surface.

    Postcoder is a paid upstream and search is a fan-out query; both run per keystroke, so the
    honest peak is a person typing (a few a second, briefly). A held-down key, a runaway retry loop
    or a script driving a stolen session is the shape being bounded; the cap is per user, so it
    never touches a colleague. No shared/tenant key: nothing here spends a per-firm budget worth
    an alarm.
    """
    return {f"user:{surface}:{user_id}": Limit(per_minute, MINUTE)}


async def enforce(surface: str, keyed_limits: Mapping[str, Limit], *, detail: str) -> None:
    """Check and raise 429 on breach — for API endpoints that can fail the request outright."""
    decision = await limiter.check(surface, keyed_limits)
    if not decision.allowed:
        raise too_many_requests(decision, detail)
