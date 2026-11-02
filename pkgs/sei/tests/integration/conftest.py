"""Guards for the tests that call SEI's real SWP test environment.

`moon run sei:integration-test`. Never part of a gate, never in CI, and excluded from a bare
`pytest` by `addopts` in pyproject — see the note there for why the exclusion lives in the config
rather than in the task's arguments.

SEI's guard differs from the other two in the one way that matters: **the environment is named,
not inferred from a URL.** `SEI_ENVIRONMENT` is `i10`/`i31`/`p04`, and `p04` is production —
`ENVIRONMENTS` maps the first two to `test.mtls.api.seic.com` and p04 to `mtls.api.seic.com`. So
this refuses on BOTH the name and the resolved host: a hand-set `SEI_BASE_URL` can point an `i31`
label at the production host, and a p04 label is production whatever the URL says. Either alone
would be a guard with a way around it.

**SEI restricts by SOURCE IP**, which no other vendor here does. A refusal is therefore as likely
to mean "this machine is not on the allowlist" as "the credentials are wrong" — the Coolify host
or a laptop on the company VPN are the two places that work (verified 03 Aug 2026, which is what
disproved "only from Coolify" and unstalled the first sync). The auth probe says which.

**Writes are off unless asked for.** SEI's writes are instructions about real money — a cash
receipt, a disbursement, an enrolment — so the default is reads even though the environment is a
test one.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Generator
from urllib.parse import urlparse

import pytest

from sei.client import SeiClient
from sei.settings import SeiSettings

_ALLOW_SKIP = os.environ.get("ALLOW_SKIP_INTEGRATION") == "1"
ALLOW_WRITES = os.environ.get("INTEGRATION_ALLOW_WRITES") == "1"

#: SEI environments these tests may touch. `p04` is production and is deliberately absent.
TEST_ENVIRONMENTS = frozenset({"i10", "i31"})

#: And the host those environments resolve to. Checked as well as the name, because
#: `SEI_BASE_URL` overrides the mapping — an `i31` label can be pointed anywhere.
TEST_HOSTS = frozenset({"test.mtls.api.seic.com"})


def _refuse(reason: str) -> None:
    if _ALLOW_SKIP:
        pytest.skip(reason)
    pytest.fail(f"{reason} — set ALLOW_SKIP_INTEGRATION=1 to skip instead of failing")


@pytest.fixture(scope="session")
def environment() -> str:
    return (os.environ.get("SEI_ENVIRONMENT") or "i31").strip().lower()


@pytest.fixture(scope="session")
def settings(environment: str) -> SeiSettings:
    """Credentials for a TEST environment, or a refusal naming what is wrong."""
    if environment not in TEST_ENVIRONMENTS:
        # Not overridable by ALLOW_SKIP_INTEGRATION: p04 is a real firm's book.
        pytest.fail(
            f"refusing to run integration tests against SEI environment {environment!r} — "
            f"only {sorted(TEST_ENVIRONMENTS)} are test environments. p04 is production."
        )

    resolved = SeiSettings.for_environment(environment)
    # A PROPERTY, not a method — `resolved.configured()` raises "'bool' object is not callable",
    # which is what the first run of this file did.
    if not resolved.configured:
        # `configured` is the client's own check, so the message names the same things
        # `__aenter__` would have refused on rather than a second opinion about what is needed.
        missing = [
            f"SEI_{name.upper()}"
            for name in (
                "base_url",
                "pfx_base64",
                "pfx_password",
                "oauth_username",
                "oauth_password",
                "appkey",
                "appsecret",
            )
            if not str(getattr(resolved, name, "") or "").strip()
        ]
        named = ", ".join(missing) or "see settings.configured"
        _refuse(
            f"SEI {environment} is not configured: {named} — the per-environment overrides "
            f"SEI_{environment.upper()}_* count too"
        )

    host = (urlparse(resolved.base_url).hostname or "").lower()
    if host not in TEST_HOSTS:
        pytest.fail(
            f"refusing to run integration tests against {host!r} — not a known SEI test host. "
            f"Known: {sorted(TEST_HOSTS)}. `SEI_BASE_URL` overrides the environment mapping, so "
            "the name alone is not enough of a guard."
        )
    return resolved


#: Select Platform. The sync requests every firm on the SFTP Reference report; this suite
#: calls Select unless ``SEI_INTEGRATION_FIRM`` names another.
SELECT_FIRM = "3106"


@pytest.fixture(scope="session")
def firm_id() -> str:
    """The firm to scope a reference read by.

    ``pkgs/sei`` holds no firm list. The sync reads firms from the Reference report.
    ``SEI_INTEGRATION_FIRM`` overrides the default, which is Select (``3106``).
    """
    explicit = (os.environ.get("SEI_INTEGRATION_FIRM") or "").strip()
    return explicit or SELECT_FIRM


@pytest.fixture(scope="session")
async def reachable(settings: SeiSettings) -> None:
    """One probe, so an unreachable environment is ONE message and not one per test.

    **It probes the HEARTBEAT, and which call this is turned out to matter enormously.**
    `/heartbeatapiproxy/status` is the only SWP call that needs no entitlement and no firm, so a
    200 from it establishes exactly what this fixture is for: the client certificate was accepted,
    the IP is allowlisted, and all three credentials work.

    It used to probe `system-codes-cache`. On 06 Sep 2026 that resource was one this AppKey was
    not entitled to, so the suite refused every test with a message blaming the VPN while the
    OAuth token endpoint and the heartbeat were both answering 200. The cache read is entitled
    now; the heartbeat stays the probe because it still needs no firm. An un-entitled resource
    is a fact about one endpoint; using one as a liveness check makes it look like a fact about
    the network.

    SEI still allowlists by SOURCE IP, which no other vendor here does, so that remains the
    commonest reason this cannot run — the Coolify host and a laptop on the company VPN are the
    two places that work. It is treated as an environment being unavailable rather than a defect:
    the same class as "the credentials are not set", so it goes through `_refuse`.
    """
    from sei._base import SeiAuthError, SeiEntitlementError

    try:
        async with SeiClient(settings) as client:
            answered = await client.heartbeat()
    except SeiEntitlementError as exc:  # pragma: no cover - the heartbeat needs no entitlement
        pytest.fail(
            f"the heartbeat itself is not entitled, which should be impossible: {exc}. If SEI has "
            "started gating /heartbeatapiproxy, this fixture needs a different liveness probe."
        )
    except SeiAuthError as exc:
        _refuse(
            f"SEI refused this machine: {exc}. SEI allowlists by SOURCE IP — connect to the "
            "company VPN (or run from the Coolify host). The heartbeat needs no entitlement, so "
            "a refusal HERE really is the machine or the certificate."
        )
    except Exception as exc:  # noqa: BLE001 - any transport failure is the environment
        _refuse(f"SEI is unreachable from here: {type(exc).__name__}: {exc}")
    else:
        if not answered:
            _refuse("SEI's heartbeat answered without OK — the platform is down or degraded")


def entitled_or_skip(exc: BaseException) -> None:
    """Re-raise unless SEI said this AppKey is not entitled to the resource; then skip.

    **An entitlement gap is a finding about ONE endpoint, not a broken test**, and it is SEI's to
    grant — the same shape as Morgan Lloyd's 85 forbidden `cam_*` tables, which
    `docs/morgan-lloyd/endpoint-coverage.md` treats as a permissions request rather than a code
    gap. A red run for a known vendor gap is noise that gets ignored, so this skips with the
    resource named and lets everything else keep its meaning.

    Deliberately NOT a blanket 401 catch: a genuine auth failure still fails, because that one IS
    ours to fix.
    """
    from sei._base import SeiEntitlementError

    if isinstance(exc, SeiEntitlementError):
        pytest.skip(f"not entitled: {exc}")
    raise exc


def degraded_or_skip(exc: BaseException) -> None:
    """The third failure mode: SEI is UP but this service is not.

    `platformavailabilityservices` answers 503 and `retrievefeepackagedetails` answers
    `500 "The service is temporarily unavailable"` on i31 today, and the monitoring dashboard
    shows the same. A test that fails on those is reporting somebody else's outage as our defect.
    """
    from sei._base import SeiApiError

    if isinstance(exc, SeiApiError) and (exc.status or 0) >= 500:
        pytest.skip(f"SEI service is degraded: {exc}")
    raise exc


@pytest.fixture
async def api(settings: SeiSettings, reachable: None) -> AsyncIterator[SeiClient]:
    async with SeiClient(settings) as client:
        yield client


@pytest.fixture
def writes_allowed() -> None:
    """Gate for a test that INSTRUCTS something — a receipt, a transfer, an enrolment."""
    if not ALLOW_WRITES:
        pytest.skip(
            "read-only run: set INTEGRATION_ALLOW_WRITES=1 to exercise the instruction paths. "
            "SEI's writes are instructions about money, even in a test environment."
        )


#: Apigee's own words when the gateway has no proxy for a path. Matched on the fault SHAPE
#: rather than on the status alone, because an ordinary 404 ("no such account") is a real answer
#: a test should be allowed to assert on.
_GATEWAY_FAULTS = ("unable to identify proxy", "unknown-resource")


def _is_gateway_fault(message: str) -> bool:
    lowered = message.lower()
    return any(fault in lowered for fault in _GATEWAY_FAULTS)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    """Turn an escaping vendor-side refusal into a skip, for every test at once.

    **Applied as a hook rather than a `try` in each test, so it cannot be forgotten** — a new test
    against a resource SEI has not entitled would otherwise go red and, being one red among
    several, get ignored along with the others.

    This is the posture `docs/morgan-lloyd/endpoint-coverage.md` takes for the 85 forbidden
    `cam_*` tables: a vendor permission we do not hold is a request to the vendor, not a defect in
    our code, and a suite that is permanently red about it stops being read. The scale of the gap
    does not disappear into the skips — `TestWhatThisAppKeyMayActuallyReach` reports it as a single
    explicit finding and FAILS the day a resource is granted.

    Three vendor-side outcomes are skipped, and they are different things:

    * `SeiEntitlementError` — the AppKey is not entitled to that RESOURCE. SEI must grant it.
    * a 5xx — SEI is up and this service is not (i31 answers 503 on `platformavailabilityservices`
      and 500 on `retrievefeepackagedetails` today, which the monitoring dashboard agrees with).
    * a 404 carrying an Apigee proxy fault — no route is DEPLOYED for a path SEI's own OpenAPI
      documents, the same class as Morgan Lloyd's documented-but-absent `cam_payrequest`.

    Everything else still fails, including a plain 401 — that one IS ours.

    `wrapper=True`, not `hookwrapper=True`: the old style raises from a TEARDOWN, which pluggy
    warns about (`PluggyTeardownRaisedWarning`) and which buries the reason in a warnings block
    instead of putting it on the skip. The new style lets the exception be swapped in place.
    """
    from sei._base import SeiApiError, SeiEntitlementError

    try:
        return (yield)
    except SeiEntitlementError as exc:
        pytest.skip(f"not entitled: {exc}")
    except SeiApiError as exc:
        if (exc.status or 0) >= 500:
            pytest.skip(f"SEI service degraded: {exc}")
        if exc.status == 404 and _is_gateway_fault(str(exc)):
            pytest.skip(f"SEI has no route deployed for this path: {exc}")
        raise
