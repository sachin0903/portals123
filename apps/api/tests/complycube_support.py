"""Shared beds for the DB-backed ComplyCube tests: a firm, a client, a fake provider.

Every key carries a per-bed nonce, and ``Bed.cleanup`` deletes what the bed created (the firm's
rows, plus the tenantless rows whose provider ids carry the nonce), so no test depends on or
disturbs another's rows.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.complycube import consumer
from app.complycube.client import ScreeningClient, environment
from app.complycube.sync import accept_event

Maker = async_sessionmaker[AsyncSession]


def bed_metadata(bed: Bed) -> dict[str, str]:
    """The metadata binding stamps on the bed's client (``client_metadata``), named for the stage
    the suite runs as now. The firm is ``seed_bed``'s ``make_tenant``."""
    return {
        "graphene_env": environment(),
        "graphene_origin": "portal",
        "graphene_firm_uid": f"ccf_{bed.nonce}",
        "graphene_firm": f"Firm ccf {bed.nonce}",
        "graphene_contact_uid": bed.contact_uid,
    }


class FakeProvider:
    """A ComplyCube stand-in. Tests edit ``checks`` / ``clients`` to change what a fetch sees.

    Given a bed, it already knows the bed's provider client.
    """

    def __init__(self, bed: Bed | None = None) -> None:
        self.checks: dict[str, dict[str, Any]] = {}
        self.clients: dict[str, dict[str, Any]] = {}
        if bed is not None:
            self.clients[bed.provider_client_id] = {
                "id": bed.provider_client_id,
                "type": "person",
                "externalId": bed.contact_uid,
                # What binding the bed's contact would have sent, so a screen finds it current.
                "personDetails": {"firstName": "Ada", "lastName": "Lovelace", "nationality": "GB"},
                "metadata": bed_metadata(bed),
            }
        self.documents: dict[str, dict[str, Any]] = {}
        self.photos: dict[str, dict[str, Any]] = {}
        self.addresses: dict[str, dict[str, Any]] = {}
        self.status_override: int | None = None
        #: The requests ``status_override`` answers, each a path or ``"METHOD path"``; None for all.
        self.status_paths: set[str] | None = None
        #: Answer ``GET /clients?externalId=…`` from ``clients``, filtered as ComplyCube filters
        #: (exact ``externalId``, exact ``metadata.*``). Off, the search answers 404: none found.
        self.searchable = False
        #: Risk profiles by provider client id; a client missing here answers 404.
        self.risk: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        #: The body of each write, with its path, in order.
        self.writes: list[tuple[str, dict[str, Any]]] = []

    def overridden(self, method: str, path: str) -> httpx.Response | None:
        """``status_override``'s answer for this request, or None when it does not apply."""
        if self.status_override is None:
            return None
        if self.status_paths is not None and not (
            path in self.status_paths or f"{method} {path}" in self.status_paths
        ):
            return None
        return httpx.Response(self.status_override, json={"message": "unavailable"})

    def _search(self, request: httpx.Request) -> httpx.Response:
        wanted = dict(request.url.params)
        items = [
            client
            for client in self.clients.values()
            if client.get("externalId") == wanted.get("externalId")
            and all(
                (client.get("metadata") or {}).get(key.removeprefix("metadata.")) == value
                for key, value in wanted.items()
                if key.startswith("metadata.")
            )
        ]
        return httpx.Response(200, json={"items": items})

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        self.calls.append(f"{request.method} {path}")
        refused = self.overridden(request.method, path)
        if refused is not None:
            return refused
        if self.searchable and request.method == "GET" and path == "/clients":
            return self._search(request)
        if request.method == "POST":
            body = json.loads(request.content or b"{}")
            self.writes.append((path, body))
            if path == "/checks":
                created = {
                    "id": f"screen_{uuid.uuid4().hex[:12]}",
                    "clientId": body.get("clientId"),
                    "type": body.get("type"),
                    "status": "pending",
                    "enableMonitoring": body.get("enableMonitoring"),
                }
                self.checks[created["id"]] = created
                return httpx.Response(200, json=created)
            held = self.clients.get(path.removeprefix("/clients/"))
            if path.startswith("/clients/") and held is not None:
                # As ComplyCube does: personDetails and companyDetails merge field by field.
                for key, value in body.items():
                    held[key] = {**held.get(key, {}), **value} if isinstance(value, dict) else value
                return httpx.Response(200, json=held)
        if path.startswith("/clients/") and path.endswith("/riskProfile"):
            profile = self.risk.get(path.removeprefix("/clients/").removesuffix("/riskProfile"))
            if profile is None:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json=profile)
        for prefix, store in (
            ("/checks/", self.checks),
            ("/clients/", self.clients),
            ("/documents/", self.documents),
            ("/livePhotos/", self.photos),
            ("/addresses/", self.addresses),
        ):
            if path.startswith(prefix):
                found = store.get(path.removeprefix(prefix))
                if found is None:
                    return httpx.Response(404, json={"message": "not found"})
                return httpx.Response(200, json=found)
        return httpx.Response(404, json={"message": "not found"})

    def client(self) -> ScreeningClient:
        async def no_sleep(seconds: float) -> None:
            del seconds

        return ScreeningClient(
            "test-key", transport=httpx.MockTransport(self._handler), sleep=no_sleep, max_retries=0
        )


@dataclass
class Bed:
    maker: Maker
    nonce: str
    tenant_id: int
    contact_id: int
    contact_uid: str
    client_pk: int
    provider_client_id: str
    webhook_id: int | None = None
    extra_tenants: list[int] = field(default_factory=list)

    def ref(self, name: str) -> str:
        """A provider id unique to this bed."""
        return f"{name}_{self.nonce}"

    async def cleanup(self) -> None:
        """Children before the tenant: every table here is audited, and audit.record() writes a
        row keyed by the tenant, so a cascading tenant delete fails on audit.log's foreign key."""
        ids = [self.tenant_id, *self.extra_tenants]
        like = f"%{self.nonce}%"
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            for stmt in (
                "DELETE FROM compliance.webhook_deliveries WHERE tenant_id = ANY(:ids)",
                "DELETE FROM compliance.matches m USING compliance.checks c "
                "  WHERE c.id = m.check_id AND (c.tenant_id = ANY(:ids) "
                "  OR c.provider_check_id LIKE :like)",
                "DELETE FROM compliance.checks WHERE tenant_id = ANY(:ids) "
                "  OR provider_check_id LIKE :like",
                "DELETE FROM compliance.documents WHERE tenant_id = ANY(:ids) "
                "  OR provider_document_id LIKE :like",
                "DELETE FROM compliance.live_photos WHERE tenant_id = ANY(:ids) "
                "  OR provider_photo_id LIKE :like",
                "DELETE FROM compliance.addresses WHERE tenant_id = ANY(:ids)",
                "DELETE FROM compliance.events WHERE tenant_id = ANY(:ids) "
                "  OR provider_event_id LIKE :like",
                "DELETE FROM compliance.clients WHERE tenant_id = ANY(:ids) "
                "  OR provider_client_id LIKE :like",
                "DELETE FROM compliance.webhooks WHERE tenant_id = ANY(:ids)",
                "DELETE FROM tasks.tasks WHERE tenant_id = ANY(:ids)",
                "DELETE FROM crm.kyc_checks WHERE tenant_id = ANY(:ids)",
                "DELETE FROM crm.contacts WHERE tenant_id = ANY(:ids)",
                "DELETE FROM auth.roles WHERE tenant_id = ANY(:ids)",
                "DELETE FROM auth.tenants WHERE id = ANY(:ids)",
            ):
                await s.execute(text(stmt), {"ids": ids, "like": like})


async def _scalar(s: AsyncSession, sql: str, params: dict[str, Any]) -> Any:
    return (await s.execute(text(sql), params)).scalar_one()


async def make_tenant(s: AsyncSession, tag: str, nonce: str) -> int:
    return int(
        await _scalar(
            s,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            {"u": f"{tag}_{nonce}", "a": f"{tag}-{nonce}", "n": f"Firm {tag} {nonce}"},
        )
    )


async def seed_bed(maker: Maker, *, webhook: bool = False) -> Bed:
    nonce = uuid.uuid4().hex[:10]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tenant_id = await make_tenant(s, "ccf", nonce)
        contact_uid = f"con{nonce}"
        contact_id = int(
            await _scalar(
                s,
                "INSERT INTO crm.contacts (tenant_id, uid, name, type, service_type, nationality) "
                "VALUES (:t, :u, 'Ada Lovelace', 'individual', 'advised', ARRAY['GB']) "
                "RETURNING id",
                {"t": tenant_id, "u": contact_uid},
            )
        )
        provider_client_id = f"cli_{nonce}"
        client_pk = int(
            await _scalar(
                s,
                "INSERT INTO compliance.clients (tenant_id, contact_id, provider_client_id) "
                "VALUES (:t, :c, :p) RETURNING id",
                {"t": tenant_id, "c": contact_id, "p": provider_client_id},
            )
        )
        webhook_id = None
        if webhook:
            webhook_id = int(
                await _scalar(
                    s,
                    "INSERT INTO compliance.webhooks (tenant_id, url, secret_ct) "
                    "VALUES (:t, 'https://firm.example/hooks', 'ciphertext') RETURNING id",
                    {"t": tenant_id},
                )
            )
    return Bed(
        maker=maker,
        nonce=nonce,
        tenant_id=tenant_id,
        contact_id=contact_id,
        contact_uid=contact_uid,
        client_pk=client_pk,
        provider_client_id=provider_client_id,
        webhook_id=webhook_id,
    )


async def insert_check(
    bed: Bed,
    provider_check_id: str,
    *,
    kind: str = "aml",
    origin: str = "partner",
    kyc_check_id: int | None = None,
    status: str = "pending",
    monitoring: bool = False,
) -> int:
    """A check row as the code that started the check writes it."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return int(
            await _scalar(
                s,
                "INSERT INTO compliance.checks (tenant_id, client_id, kind, status, "
                "  provider_check_id, origin, kyc_check_id, monitoring) "
                "VALUES (:t, :c, :k, :st, :p, :o, :kyc, :m) RETURNING id",
                {
                    "t": bed.tenant_id,
                    "c": bed.client_pk,
                    "k": kind,
                    "st": status,
                    "p": provider_check_id,
                    "o": origin,
                    "kyc": kyc_check_id,
                    "m": monitoring,
                },
            )
        )


async def insert_kyc_check(bed: Bed, *, kind: str = "aml") -> int:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return int(
            await _scalar(
                s,
                "INSERT INTO crm.kyc_checks (tenant_id, contact_id, kind, status, provider) "
                "VALUES (:t, :c, :k, 'pending', 'complycube') RETURNING id",
                {"t": bed.tenant_id, "c": bed.contact_id, "k": kind},
            )
        )


def screening_check(
    bed: Bed,
    provider_check_id: str,
    *,
    status: str = "complete",
    outcome: str | None = "clear",
    matches: list[dict[str, Any]] | None = None,
    monitoring: bool | None = None,
) -> dict[str, Any]:
    check: dict[str, Any] = {
        "id": provider_check_id,
        "clientId": bed.provider_client_id,
        "type": "extensive_screening_check",
        "status": status,
        "result": {"outcome": outcome, "breakdown": {"matches": matches or []}},
    }
    if monitoring is not None:
        check["enableMonitoring"] = monitoring
    return check


def event(bed: Bed, name: str, event_type: str, provider_check_id: str) -> dict[str, Any]:
    return {
        "id": f"evt_{name}_{bed.nonce}",
        "type": event_type,
        "payload": {"id": provider_check_id, "status": "complete"},
    }


async def accept(maker: Maker, payload: dict[str, Any]) -> int | None:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return await accept_event(s, payload)


async def drain(maker: Maker, provider: FakeProvider) -> consumer.DrainResult | None:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return await consumer.drain_once(s, provider.client())


async def fetch(maker: Maker, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        rows = (await s.execute(text(sql), params)).mappings().all()
    return [dict(row) for row in rows]


def as_json(value: object) -> Any:
    return json.loads(value) if isinstance(value, str) else value
