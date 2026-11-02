"""The provider client a partner check creates, against a real Postgres.

The firm's uid and name come from ``auth.tenants`` with the client row, so the create is stamped
with them as well as this deployment's name and our ids for the client.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.complycube.client import ScreeningClient
from app.config import settings
from external.live import LiveStore
from external.models import CheckCreate
from tests.complycube_support import Bed, Maker, fetch, seed_bed


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def bed(maker: Maker) -> AsyncIterator[Bed]:
    seeded = await seed_bed(maker)
    yield seeded
    await seeded.cleanup()


async def test_a_partner_check_stamps_the_new_provider_client_with_the_firm(
    bed: Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "app_stage", "staging")
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        # Not yet at the provider, with an address (AML needs a country) and the firm's own ids.
        await s.execute(
            text("UPDATE compliance.clients SET provider_client_id = NULL WHERE id = :c"),
            {"c": bed.client_pk},
        )
        await s.execute(
            text(
                "INSERT INTO compliance.addresses (tenant_id, client_id, country) "
                "VALUES (:t, :c, 'GB')"
            ),
            {"t": bed.tenant_id, "c": bed.client_pk},
        )
        await s.execute(
            text("UPDATE crm.contacts SET data = CAST(:d AS jsonb) WHERE id = :c"),
            {
                "d": json.dumps({"reference": "FIRM-REF-1", "metadata": {"crm_id": "crm-9"}}),
                "c": bed.contact_id,
            },
        )
    rows = await fetch(
        bed.maker, "SELECT uid FROM compliance.clients WHERE id = :c", {"c": bed.client_pk}
    )
    client_uid = str(rows[0]["uid"])

    new_id = bed.ref("cli_new")
    seen: list[str] = []
    created: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        seen.append(f"{request.method} {path}?{request.url.query.decode()}")
        if request.method == "GET" and path == "/clients":
            return httpx.Response(200, json={"items": []})
        if request.method == "POST" and path == "/clients":
            created.append(json.loads(request.content))
            return httpx.Response(200, json={"id": new_id})
        return httpx.Response(404, json={"message": "missing"})

    screening = ScreeningClient("test-key", transport=httpx.MockTransport(handler))
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        bound = await LiveStore(s, screening=screening).bind_provider_client(
            client_uid, CheckCreate(kind="aml"), tenant_id=bed.tenant_id
        )

    assert bound is True
    assert [item["metadata"] for item in created] == [
        {
            "graphene_env": "staging",
            "graphene_origin": "partner",
            "graphene_firm_uid": f"ccf_{bed.nonce}",
            "graphene_firm": f"Firm ccf {bed.nonce}",
            "graphene_contact_uid": bed.contact_uid,
            "graphene_client_uid": client_uid,
            "graphene_firm_reference": "FIRM-REF-1",
        }
    ]
    assert "crm_id" not in json.dumps(created[0])
    assert any(
        f"externalId={bed.contact_uid}" in item and "metadata.graphene_env=staging" in item
        for item in seen
    )
    stored = await fetch(
        bed.maker,
        "SELECT provider_client_id FROM compliance.clients WHERE id = :c",
        {"c": bed.client_pk},
    )
    assert stored == [{"provider_client_id": new_id}]
