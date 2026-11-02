"""Guards for the tests that call Seccl's real staging API.

`moon run seccl:integration-test`. Never part of a gate, never in CI, and excluded from a bare
`pytest` by `addopts` in pyproject — see the note there for why the exclusion is in the config
rather than in the task's arguments.

The same three guards as the Morgan Lloyd suite, with one difference worth stating: **Seccl has
delete**, so a write probe here is recoverable in a way an ML one is not. That makes the write
gate a matter of not littering staging rather than of permanence — still off by default, but for
a smaller reason.

**NON-PRODUCTION, FAIL-CLOSED.** `api_url` has to be a known staging host. An allowlist rather
than a "does it look like prod" test, for the reason `app/email.py`'s send guard gives: the
absence of recognisable configuration must mean "do nothing", so an unfamiliar host lands on the
guarded side. Production is `pfolio-api.seccl.tech` and staging is `pfolio-api-staging.seccl.tech`
— one hyphenated word apart, which is exactly why substring matching is not good enough.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import pytest

from seccl.client import SecclClient
from seccl.settings import SecclSettings

_ALLOW_SKIP = os.environ.get("ALLOW_SKIP_INTEGRATION") == "1"
ALLOW_WRITES = os.environ.get("INTEGRATION_ALLOW_WRITES") == "1"

#: Seccl API hosts these tests may touch.
SANDBOX_HOSTS = frozenset({"pfolio-api-staging.seccl.tech"})


def _refuse(reason: str) -> None:
    if _ALLOW_SKIP:
        pytest.skip(reason)
    pytest.fail(f"{reason} — set ALLOW_SKIP_INTEGRATION=1 to skip instead of failing")


@pytest.fixture(scope="session")
def settings() -> SecclSettings:
    """Credentials for staging, or a refusal naming what is wrong."""
    resolved = SecclSettings()

    if not resolved.firm_id.strip():
        _refuse("SECCL_FIRM_ID is not set")
    # Two auth shapes: the JWT client-assertion one (kid + private key) and the legacy
    # user/password. Either is enough to attempt a call, so this asks for one of them rather
    # than for a specific pair — naming both is what makes the message useful.
    modern = (
        resolved.client_id.strip()
        and resolved.kid.strip()
        and (resolved.private_key_b64.strip() or resolved.private_key_path.strip())
    )
    legacy = resolved.user_id.strip() and resolved.password.strip()
    if not (modern or legacy):
        _refuse(
            "Seccl credentials are not set: either SECCL_CLIENT_ID + SECCL_KID + "
            "SECCL_PRIVATE_KEY_B64 (the client-assertion flow), or SECCL_USER_ID + "
            "SECCL_PASSWORD (legacy)"
        )

    host = (urlparse(resolved.api_url).hostname or "").lower()
    if host not in SANDBOX_HOSTS:
        # NOT overridable by ALLOW_SKIP_INTEGRATION: a run pointed at an unrecognised host is a
        # configuration error, not an environment that happens to be unavailable.
        pytest.fail(
            f"refusing to run integration tests against {host!r} — not a known Seccl staging "
            f"host. Known: {sorted(SANDBOX_HOSTS)}. Add one above only when you know it is not "
            "production; a firm's real book is one hyphen away."
        )
    return resolved


@pytest.fixture
async def api(settings: SecclSettings) -> AsyncIterator[SecclClient]:
    async with SecclClient(settings=settings) as client:
        yield client


@pytest.fixture
def writes_allowed() -> None:
    """Gate for a test that CREATES something in staging."""
    if not ALLOW_WRITES:
        pytest.skip("read-only run: set INTEGRATION_ALLOW_WRITES=1 to exercise the write paths")
