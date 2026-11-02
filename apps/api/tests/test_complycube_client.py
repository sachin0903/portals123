"""The ComplyCube HTTP client: request bodies, one shared connection, retries, configuration."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from app.complycube import client as client_mod
from app.complycube.client import (
    ComplyCubeNotConfigured,
    ScreeningClient,
    complycube_configured,
    monitoring_of,
    require_client,
)


def _recording(
    replies: list[httpx.Response] | None = None,
) -> tuple[ScreeningClient, list[tuple[str, str, dict[str, Any]]], list[float]]:
    seen: list[tuple[str, str, dict[str, Any]]] = []
    sleeps: list[float] = []
    queue = list(replies or [])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request.method, request.url.path.removeprefix("/v1"), body))
        assert request.headers["authorization"] == "test-key"
        if queue:
            return queue.pop(0)
        return httpx.Response(200, json={"id": "ok"})

    async def no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = ScreeningClient(
        "test-key", transport=httpx.MockTransport(handler), sleep=no_sleep, max_retries=3
    )
    return client, seen, sleeps


async def test_create_screening_sends_the_monitoring_flag() -> None:
    client, seen, _ = _recording()
    await client.create_screening("cli_1", monitoring=True)
    await client.create_screening("cli_1", monitoring=False)
    assert [body for _, _, body in seen] == [
        {"clientId": "cli_1", "type": "extensive_screening_check", "enableMonitoring": True},
        {"clientId": "cli_1", "type": "extensive_screening_check", "enableMonitoring": False},
    ]


#: ComplyCube's answer to a screen that asks for monitoring on a plan without it.
NOT_IN_PLAN = httpx.Response(
    403,
    json={
        "type": {"type": "unauthorized", "message": "Your current plan does not include access"},
        "message": "Continuous monitoring is not available in your current plan.",
    },
)


async def test_a_plan_without_monitoring_screens_without_it() -> None:
    client, seen, _ = _recording([NOT_IN_PLAN, httpx.Response(200, json={"id": "chk_1"})])
    created = await client.create_screening("cli_1", monitoring=True)
    assert [body["enableMonitoring"] for _, _, body in seen] == [True, False]
    assert created == {"id": "chk_1", "enableMonitoring": False}
    assert monitoring_of(created, True) is False
    assert monitoring_of({"id": "chk_2"}, True) is True
    assert monitoring_of({"id": "chk_3", "enableMonitoring": True}, False) is True


async def test_any_other_refusal_of_a_screen_is_raised() -> None:
    forbidden = httpx.Response(403, json={"message": "Forbidden."})
    client, seen, _ = _recording([forbidden])
    with pytest.raises(httpx.HTTPStatusError):
        await client.create_screening("cli_1", monitoring=True)
    assert len(seen) == 1
    # Not asked for monitoring: a plan refusal is not retried either.
    client, seen, _ = _recording([NOT_IN_PLAN])
    with pytest.raises(httpx.HTTPStatusError):
        await client.create_screening("cli_1", monitoring=False)
    assert len(seen) == 1


async def test_validate_check_leaves_out_what_was_not_given() -> None:
    client, seen, _ = _recording()
    await client.validate_check("chk_1", "clear")
    await client.validate_check("chk_1", "rejected", match_id="m_1", comment="True match.")
    assert seen == [
        ("POST", "/checks/chk_1/validate", {"outcome": "clear"}),
        (
            "POST",
            "/checks/chk_1/validate",
            {"outcome": "rejected", "matchId": "m_1", "comment": "True match."},
        ),
    ]


async def test_update_monitoring_posts_to_the_check() -> None:
    client, seen, _ = _recording()
    await client.update_monitoring("chk_1", enabled=True)
    assert seen == [("POST", "/checks/chk_1", {"enableMonitoring": True})]


async def test_identity_uploads_the_back_of_a_document_when_there_is_one() -> None:
    client, seen, _ = _recording(
        [
            httpx.Response(200, json={"id": "doc_1"}),
            httpx.Response(200, json={"id": "img_f"}),
            httpx.Response(200, json={"id": "img_b"}),
            httpx.Response(200, json={"id": "photo_1"}),
            httpx.Response(200, json={"id": "chk_1"}),
        ]
    )
    await client.submit_identity(
        "cli_1",
        document_type="drivers_license",
        issuing_country="GB",
        document=b"front",
        document_file_name="front.png",
        selfie=b"selfie",
        document_back=b"back",
        document_back_file_name="reverse",
    )
    paths = [path for _, path, _ in seen]
    assert paths == [
        "/documents",
        "/documents/doc_1/upload/front",
        "/documents/doc_1/upload/back",
        "/livePhotos",
        "/checks",
    ]
    back = seen[2][2]
    assert back["fileName"] == "back.jpg"
    assert base64.b64decode(back["data"]) == b"back"


async def test_a_read_retries_a_5xx_and_a_429_with_back_off() -> None:
    client, seen, sleeps = _recording(
        [
            httpx.Response(503),
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"id": "chk_1"}),
        ]
    )
    got = await client.get_check("chk_1")
    assert got == {"id": "chk_1"}
    assert len(seen) == 3
    assert sleeps == [0.5, 2.0]


async def test_a_read_gives_up_after_the_retries() -> None:
    client, seen, _ = _recording([httpx.Response(503)] * 5)
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_check("chk_1")
    assert len(seen) == 4


async def test_a_write_is_not_resent_after_a_5xx() -> None:
    """A POST that reached the provider may have created a paid check."""
    client, seen, _ = _recording([httpx.Response(502), httpx.Response(200, json={"id": "x"})])
    with pytest.raises(httpx.HTTPStatusError):
        await client.create_screening("cli_1", monitoring=False)
    assert len(seen) == 1


async def test_a_write_is_resent_after_a_429() -> None:
    client, seen, _ = _recording([httpx.Response(429), httpx.Response(200, json={"id": "x"})])
    created = await client.create_screening("cli_1", monitoring=False)
    assert created == {"id": "x"}
    assert len(seen) == 2


async def test_a_404_is_not_retried() -> None:
    client, seen, _ = _recording([httpx.Response(404, json={"message": "missing"})])
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_check("chk_gone")
    assert len(seen) == 1


async def test_every_call_shares_one_connection_pool() -> None:
    client, _, _ = _recording()
    await client.get_check("chk_1")
    first = client._http()
    await client.get_check("chk_2")
    assert client._http() is first
    await client.aclose()


async def test_a_blank_key_raises_without_a_request() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    client = ScreeningClient("", transport=httpx.MockTransport(handler))
    with pytest.raises(ComplyCubeNotConfigured, match="ComplyCube is not configured."):
        await client.get_check("chk_1")
    assert seen == []


def test_require_client_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(client_mod, "_shared", None)
    assert not complycube_configured()
    with pytest.raises(ComplyCubeNotConfigured):
        require_client()
    monkeypatch.setattr(settings, "complycube_api_key", "live-key")
    first = require_client()
    assert require_client() is first
    monkeypatch.setattr(settings, "complycube_api_key", "rotated-key")
    assert require_client() is not first


@pytest.mark.parametrize("stage", ["dev", "staging"])
def test_a_live_key_is_unused_outside_production(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """Real checks are billed and hold real people's data: only production may send them."""
    from app.config import settings

    monkeypatch.setattr(client_mod, "_shared", None)
    monkeypatch.setattr(settings, "app_stage", stage)
    monkeypatch.setattr(settings, "complycube_api_key", "live_abc123")
    assert not complycube_configured()
    with pytest.raises(ComplyCubeNotConfigured) as refused:
        require_client()
    assert "live ComplyCube key is set outside production" in str(refused.value)
    assert client_mod.screening_from_env().configured is False


def test_a_live_key_is_used_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(client_mod, "_shared", None)
    monkeypatch.setattr(settings, "app_stage", "production")
    monkeypatch.setattr(settings, "complycube_api_key", "live_abc123")
    assert complycube_configured()
    assert require_client().configured


def test_a_live_key_is_used_outside_production_when_chosen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(client_mod, "_shared", None)
    monkeypatch.setattr(settings, "app_stage", "dev")
    monkeypatch.setattr(settings, "complycube_api_key", "live_abc123")
    monkeypatch.setattr(settings, "complycube_allow_live_key", True)
    assert complycube_configured()
    assert require_client().configured


def test_a_sandbox_key_is_used_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(client_mod, "_shared", None)
    monkeypatch.setattr(settings, "app_stage", "dev")
    monkeypatch.setattr(settings, "complycube_api_key", "test_abc123")
    assert complycube_configured()


# ---- what identifies a client as ours ----------------------------------------------------------


def test_client_metadata_names_the_deployment_firm_and_our_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_mod.settings, "app_stage", " staging ")
    assert client_mod.client_metadata(
        origin="partner",
        firm_uid="ten_abc",
        firm="Firm A",
        contact_uid="con123456789",
        client_uid="cli123456789",
        reference="  CRM-42 ",
    ) == {
        "graphene_env": "staging",
        "graphene_origin": "partner",
        "graphene_firm_uid": "ten_abc",
        "graphene_firm": "Firm A",
        "graphene_contact_uid": "con123456789",
        "graphene_client_uid": "cli123456789",
        "graphene_firm_reference": "CRM-42",
    }


def test_client_metadata_leaves_out_what_we_do_not_hold_and_fits_complycubes_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_mod.settings, "app_stage", "dev")
    meta = client_mod.client_metadata(
        origin="portal", firm_uid="ten_abc", firm="F" * 600, contact_uid="con1", reference=" "
    )
    # No client uid and a blank reference: both absent.
    assert set(meta) == {
        "graphene_env",
        "graphene_origin",
        "graphene_firm_uid",
        "graphene_firm",
        "graphene_contact_uid",
    }
    assert len(meta["graphene_firm"]) == client_mod.METADATA_VALUE_LIMIT
    assert all(len(key) <= 40 and key.startswith("graphene_") for key in meta)


def test_client_payload_carries_the_metadata() -> None:
    body = client_mod.client_payload(
        {"first_name": "Ada"}, external_id="con1", metadata={"graphene_origin": "portal"}
    )
    assert body["metadata"] == {"graphene_origin": "portal"}
    assert "metadata" not in client_mod.client_payload({}, external_id="con1")


async def test_find_by_external_id_keeps_to_this_deployment_when_named() -> None:
    urls: list[str] = []
    items = [
        {"id": "cli_other", "externalId": "con1", "metadata": {"graphene_env": "dev"}},
        {"id": "cli_bare", "externalId": "con1"},
        {"id": "cli_ours", "externalId": "con1", "metadata": {"graphene_env": "staging"}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"items": items})

    client = ScreeningClient("test-key", transport=httpx.MockTransport(handler), max_retries=0)

    assert await client.find_client_by_external_id("con1", env="staging") == "cli_ours"
    assert urls[-1].endswith("/clients?externalId=con1&metadata.graphene_env=staging")
    # Unnamed, the first with the uid, as before.
    assert await client.find_client_by_external_id("con1") == "cli_other"
    assert urls[-1].endswith("/clients?externalId=con1")
