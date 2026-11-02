"""Process-memory stand-in for the firm API.

``POST`` then ``GET`` returns the same resource. Nothing here is durable, and
every response from this mode is marked with ``X-Graphene-Stub``. The live
store in ``external.live`` implements the same operations against Postgres.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Protocol

from app.ids import new_id
from external.models import (
    ID_DOCUMENT_TYPES,
    CheckCreate,
    CheckOut,
    ChecksOut,
    ClientCreate,
    ClientOut,
    ClientPatch,
    CustodianPut,
    DocumentOut,
    HeldDocument,
    WebhookCreated,
    WebhookOut,
    apply_patch,
    checks_overview,
    identity_details,
    screened_name,
    to_client_out,
    utc_now,
)

DOCUMENTS_REQUIRED = (
    "An identity check needs a selfie and a passport, driving licence, or identity document."
)
ADDRESS_COUNTRY_REQUIRED = "An AML check needs an address country."


class Rejected(Exception):
    """A write the firm API refuses before it touches a row."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ClientStore(Protocol):
    async def create_client(self, body: ClientCreate, *, tenant_id: int) -> ClientOut: ...

    async def get_client(self, uid: str, *, tenant_id: int) -> ClientOut | None: ...

    async def list_clients(self, *, tenant_id: int, limit: int, offset: int) -> list[ClientOut]: ...

    async def patch_client(
        self, uid: str, body: ClientPatch, *, tenant_id: int
    ) -> ClientOut | None: ...

    async def create_document(
        self, client_uid: str, body: HeldDocument, *, tenant_id: int
    ) -> DocumentOut: ...

    async def create_check(
        self, client_uid: str, body: CheckCreate, *, tenant_id: int
    ) -> CheckOut: ...

    async def get_check(self, uid: str, *, tenant_id: int) -> CheckOut | None: ...

    async def checks_for(self, client_uid: str, *, tenant_id: int) -> ChecksOut: ...

    async def put_custodian(
        self, uid: str, body: CustodianPut, *, tenant_id: int
    ) -> ClientOut | None: ...

    async def get_webhook(self, *, tenant_id: int) -> WebhookOut: ...

    async def put_webhook(self, url: str, *, tenant_id: int) -> WebhookCreated: ...

    async def delete_webhook(self, *, tenant_id: int) -> WebhookOut: ...


@dataclass
class StubStore:
    clients: dict[str, ClientOut] = field(default_factory=dict)
    checks: dict[str, CheckOut] = field(default_factory=dict)
    check_clients: dict[str, str] = field(default_factory=dict)
    check_order: dict[str, int] = field(default_factory=dict)
    documents: dict[str, list[tuple[str, HeldDocument]]] = field(default_factory=dict)
    webhooks: dict[int, tuple[str, str]] = field(default_factory=dict)
    _seq: int = 0

    def reset(self) -> None:
        self.clients.clear()
        self.checks.clear()
        self.check_clients.clear()
        self.check_order.clear()
        self.documents.clear()
        self.webhooks.clear()
        self._seq = 0

    async def create_client(self, body: ClientCreate, *, tenant_id: int) -> ClientOut:
        del tenant_id
        client = to_client_out(body, uid=new_id())
        self.clients[client.uid] = client
        return client

    async def get_client(self, uid: str, *, tenant_id: int) -> ClientOut | None:
        del tenant_id
        return self.clients.get(uid)

    async def list_clients(self, *, tenant_id: int, limit: int, offset: int) -> list[ClientOut]:
        del tenant_id
        rows = list(self.clients.values())
        return rows[offset : offset + limit]

    async def patch_client(
        self, uid: str, body: ClientPatch, *, tenant_id: int
    ) -> ClientOut | None:
        del tenant_id
        current = self.clients.get(uid)
        if current is None:
            return None
        updated = apply_patch(current, body)
        self.clients[uid] = updated
        return updated

    async def create_document(
        self, client_uid: str, body: HeldDocument, *, tenant_id: int
    ) -> DocumentOut:
        del tenant_id
        client = self.clients.get(client_uid)
        if client is None:
            raise Rejected("not_found", "No client with that id.", 404)
        uid = new_id()
        self.documents.setdefault(client.uid, []).append((uid, body))
        return DocumentOut(
            uid=uid, type=body.type, side=body.side, issuing_country=body.issuing_country
        )

    async def create_check(self, client_uid: str, body: CheckCreate, *, tenant_id: int) -> CheckOut:
        del tenant_id
        client = self.clients.get(client_uid)
        if client is None:
            raise Rejected("not_found", "No client with that id.", 404)
        stored = self.documents.get(client.uid, [])
        if body.kind == "identity":
            selfie = any(item.type == "selfie" for _uid, item in stored)
            front = any(
                item.type in ID_DOCUMENT_TYPES and item.side == "front" for _uid, item in stored
            )
            if not (selfie and front):
                raise Rejected("documents_required", DOCUMENTS_REQUIRED, 422)
            document_uids, issuing_country = identity_details(
                [(uid, item.type, item.issuing_country) for uid, item in stored]
            )
            now = utc_now()
            check = CheckOut(
                uid=new_id(),
                kind=body.kind,
                status="pending",
                created_at=now,
                updated_at=now,
                document_uids=document_uids,
                issuing_country=issuing_country,
            )
        else:
            if client.address is None:
                raise Rejected("address_country_required", ADDRESS_COUNTRY_REQUIRED, 422)
            now = utc_now()
            check = CheckOut(
                uid=new_id(),
                kind=body.kind,
                status="pending",
                created_at=now,
                updated_at=now,
                name=screened_name(client),
                dob=client.dob,
                country=client.address.country,
            )
        self._seq += 1
        self.checks[check.uid] = check
        self.check_clients[check.uid] = client.uid
        self.check_order[check.uid] = self._seq
        return check

    async def get_check(self, uid: str, *, tenant_id: int) -> CheckOut | None:
        del tenant_id
        return self.checks.get(uid)

    async def checks_for(self, client_uid: str, *, tenant_id: int) -> ChecksOut:
        del tenant_id
        rows = [
            row for uid, row in self.checks.items() if self.check_clients.get(uid) == client_uid
        ]
        rows.sort(key=lambda row: (row.created_at, self.check_order[row.uid]), reverse=True)
        return checks_overview(rows)

    async def put_custodian(
        self, uid: str, body: CustodianPut, *, tenant_id: int
    ) -> ClientOut | None:
        del tenant_id
        current = self.clients.get(uid)
        if current is None:
            return None
        kept = [item for item in current.custodians if item.provider != body.provider]
        kept.append(body)
        updated = current.model_copy(update={"custodians": kept})
        self.clients[uid] = updated
        return updated

    async def get_webhook(self, *, tenant_id: int) -> WebhookOut:
        found = self.webhooks.get(tenant_id)
        if found is None:
            return WebhookOut(url=None)
        return WebhookOut(url=found[0])

    async def put_webhook(self, url: str, *, tenant_id: int) -> WebhookCreated:
        secret = secrets.token_urlsafe(24)
        self.webhooks[tenant_id] = (url, secret)
        return WebhookCreated(url=url, secret=secret)

    async def delete_webhook(self, *, tenant_id: int) -> WebhookOut:
        self.webhooks.pop(tenant_id, None)
        return WebhookOut(url=None)
