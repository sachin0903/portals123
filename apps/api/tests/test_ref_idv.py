"""``GET /ref/idv`` — how this deployment can verify an identity.

The journey kickoff's identity step reads this to say which path a client will take, so the answer
must track the same ComplyCube key the onboarding seed keys on (``complycube_configured()``) and
must require a signed-in caller. It also carries what a MANUAL check collects, because the adviser
choosing that path has to be able to tell the client what to send — from the same definition the
client is later shown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import Principal, get_current_principal
from app.config import settings
from app.main import app

_PRINCIPAL = Principal(
    user_id="user_ref_idv_test",
    organisation_id="org_ref_idv_test",
    role="adviser",
    permissions=[],
    client_id="client_ref_idv_test",
)


@pytest.fixture
def as_user() -> Iterator[None]:
    app.dependency_overrides[get_current_principal] = lambda: _PRINCIPAL
    yield
    app.dependency_overrides.pop(get_current_principal, None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_a_complycube_key_reports_available(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "complycube_api_key", "key-1")
    r = await client.get("/ref/idv")
    assert r.status_code == 200
    assert r.json()["available"] is True


@pytest.mark.asyncio
async def test_without_a_complycube_key_the_digital_check_is_not_available(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reporting it as available would promise the adviser a digital path the seed did not
    publish and preflight would refuse."""
    for blank in ("", "   "):
        monkeypatch.setattr(settings, "complycube_api_key", blank)
        r = await client.get("/ref/idv")
        assert r.status_code == 200
        assert r.json()["available"] is False


@pytest.mark.asyncio
async def test_the_manual_documents_come_whether_or_not_a_digital_check_exists(
    client: AsyncClient, as_user: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manual check is always possible, so what it asks for is always answerable.

    Both are needed either way: with no digital provider it is the only path, and with one it is
    what the client may ask for instead — and the adviser is the one who has to say what to expect.
    """
    for key in ("key-1", ""):
        monkeypatch.setattr(settings, "complycube_api_key", key)
        docs = (await client.get("/ref/idv")).json()["manual_documents"]
        # Photo ID first: it is the harder one to find, and a proof of address is worthless
        # without it.
        assert [d["key"] for d in docs] == ["photo_id", "proof_of_address"]
        assert all(d["title"] and d["guidance"] and d["format_hint"] for d in docs)


@pytest.mark.asyncio
async def test_auth_is_required(client: AsyncClient) -> None:
    r = await client.get("/ref/idv")
    assert r.status_code in (401, 403)
