"""Guards for the tests that call Morgan Lloyd's real Dynamics org.

`moon run morgan-lloyd:integration-test`. Never part of a gate, never in CI, and excluded from a
bare `pytest` by `addopts` in pyproject — see the note there for why the exclusion lives in the
config rather than in the task's arguments.

THREE GUARDS, AND THE MIDDLE ONE IS THE IMPORTANT ONE.

**Credentials, or a failure naming what is missing.** A silent skip on a task you invoked
deliberately is worse than a failure: it reads as "the integration is fine". Mirrors
`apps/api/tests/conftest.py`'s posture — skipping is possible but has to be asked for
(`ALLOW_SKIP_INTEGRATION=1`), so `moon run :integration-test` across three vendors on a laptop
with one set of credentials is workable without the skip ever being the default.

**NON-PRODUCTION, FAIL-CLOSED.** The org has to be on an allowlist of known sandboxes; anything
unrecognised refuses. Not a "does it look like production" test, which is the wrong way round —
this is the posture `app/email.py`'s send guard takes, and for the same reason: the absence of
configuration must mean "do nothing", never "do it to everyone". A misspelt or newly-issued host
lands on the guarded side, and adding a sandbox is a deliberate edit here.

It matters more for Morgan Lloyd than for either custodian because **Dynamics has no delete.**
A create against a real firm's org is permanent — `docs/morgan-lloyd/` records what a duplicated
contact cost — so the blast radius of pointing these at the wrong host is a human being in
somebody's CRM for ever.

**Writes are off unless asked for.** Everything in the default run is a READ. `cam_createcontact`
and friends run only with `INTEGRATION_ALLOW_WRITES=1`, and even then they leave records nobody
can remove: there is no cleanup and this file does not pretend otherwise.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import pytest

from morgan_lloyd.client import MorganLloydClient
from morgan_lloyd.settings import MorganLloydSettings

_ALLOW_SKIP = os.environ.get("ALLOW_SKIP_INTEGRATION") == "1"
ALLOW_WRITES = os.environ.get("INTEGRATION_ALLOW_WRITES") == "1"

#: Dynamics orgs these tests may touch. AN ALLOWLIST, and deliberately short.
#:
#: `cliftonassetdev` is the sandbox Clifton issued us; the production org differs by that one
#: word, which is precisely why a substring test like "does the host contain dev" is not good
#: enough — it would pass for a host called `cliftonasset-devon` and, worse, its inverse would
#: pass for anything unfamiliar.
SANDBOX_HOSTS = frozenset({"cliftonassetdev.crm11.dynamics.com"})


def _refuse(reason: str) -> None:
    if _ALLOW_SKIP:
        pytest.skip(reason)
    pytest.fail(f"{reason} — set ALLOW_SKIP_INTEGRATION=1 to skip instead of failing")


@pytest.fixture(scope="session")
def settings() -> MorganLloydSettings:
    """Credentials for the sandbox, or a refusal naming what is wrong."""
    resolved = MorganLloydSettings()

    missing = [
        name
        for name in ("tenant_id", "client_id", "client_secret")
        if not getattr(resolved, name, "").strip()
    ]
    if missing:
        _refuse(
            "Morgan Lloyd credentials are not set: "
            + ", ".join(f"MORGAN_LLOYD_{name.upper()}" for name in missing)
        )

    host = (urlparse(resolved.resource_url).hostname or "").lower()
    if host not in SANDBOX_HOSTS:
        # NOT a skip, and not overridable by ALLOW_SKIP_INTEGRATION: a run pointed at an
        # unrecognised org is a configuration error, and Dynamics has no delete.
        pytest.fail(
            f"refusing to run integration tests against {host!r} — not a known Morgan Lloyd "
            f"sandbox. Known: {sorted(SANDBOX_HOSTS)}. Dynamics has no delete, so an "
            "unrecognised org is refused rather than assumed to be safe; add it above only "
            "when you know it is a sandbox."
        )
    return resolved


@pytest.fixture
async def api(settings: MorganLloydSettings) -> AsyncIterator[MorganLloydClient]:
    """A live client against the sandbox.

    FUNCTION-scoped, deliberately. A session-scoped client looks like the obvious economy — one
    OAuth token instead of forty — and does not work: the client holds an `asyncio.Event` for its
    token refresh, pytest-asyncio gives each test its own event loop by default, and the second
    test dies with "is bound to a different event loop". The lifecycle suite gets its
    once-per-session behaviour by memoising the resolved IDS instead (`anchors._CACHE`), which
    are plain strings and belong to no loop.
    """
    async with MorganLloydClient(settings=settings) as client:
        yield client


@pytest.fixture
def writes_allowed() -> None:
    """Gate for a test that CREATES something. There is no cleanup and cannot be."""
    if not ALLOW_WRITES:
        pytest.skip(
            "read-only run: set INTEGRATION_ALLOW_WRITES=1 to exercise the create paths. "
            "Dynamics has no delete, so anything these make is permanent."
        )
