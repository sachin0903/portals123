"""The ComplyCube review console (``app.routers.aml``), against a real Postgres.

Drives the router's own dependency and endpoint coroutines, with ComplyCube replaced by an
``httpx.MockTransport``. The things worth proving here are the gate, the views, and what a decision
does downstream:

* ``aml:read`` is an operations-portal permission, so a firm principal is refused outright. The
  console spans firms; nothing else may open it.
* the client list puts every client that needs a human first, and the client page shows the
  latest check of each kind beside every earlier one.
* a client is keyed by its contact uid wherever it has one, and every older form of its key still
  opens its page.
* a screen is idempotent: an open check is returned rather than a second paid screen.
* an AML check settles from its matches: every one dismissed is a clear (and the portal kyc row
  passes). Any confirmed HOLDS a portal check: it stays an undecided refer, the kyc row stays
  pending, and only an operator allowing the journey to continue passes it. A partner check with a
  confirmed match is decided at once.
* a technical failure is run again, never decided as clear.
* "Ask for new photos" is only for an undecided portal identity refer with tries left, and the
  operator's note never reaches the audit subject.
* an identity check's images are listed from ComplyCube and read from it each time one is shown,
  never cached; showing a check that lists them is one logged view, and serving one is none.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import branding
from app.auth import Principal
from app.complycube import decisions, routing, screen, tasks
from app.complycube.client import ComplyCubeNotConfigured, ScreeningClient
from app.complycube.images import ImagePart
from app.config import settings
from app.routers import aml as aml_router
from tests.complycube_support import (
    Bed,
    FakeProvider,
    Maker,
    as_json,
    bed_metadata,
    fetch,
    insert_kyc_check,
    make_tenant,
    seed_bed,
)

ALL = ["aml:read", "aml:write", "aml:adjudicate"]

JPEG = b"\xff\xd8\xff\xe0" + b"\x02" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 32


class Provider(FakeProvider):
    """``FakeProvider`` plus the writes the console makes, and the images it reads."""

    def __init__(self, bed: Bed) -> None:
        super().__init__(bed)
        self.bed = bed
        self.created = 0
        self.writes: list[tuple[str, dict[str, Any]]] = []
        #: What an image download answers, by its path (``/documents/{id}/download/{side}``,
        #: ``/livePhotos/{id}/download``); a path missing here answers 404.
        self.downloads: dict[str, dict[str, Any]] = {}

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if request.method == "GET" and "/download" in path and self.overridden("GET", path) is None:
            self.calls.append(f"GET {path}")
            found = self.downloads.get(path)
            if found is None:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json=found)
        if request.method == "POST":
            body = json.loads(request.content or b"{}")
            self.calls.append(f"POST {path}")
            self.writes.append((path, body))
            refused = self.overridden("POST", path)
            if refused is not None:
                return refused
            if path == "/clients":
                found = f"cli_new_{self.bed.nonce}"
                self.clients[found] = {"id": found, **body}
                return httpx.Response(201, json={"id": found})
            if path.startswith("/clients/") and path.count("/") == 2:
                # An update, as the sandbox answers one: `type` refused, details merged.
                held = self.clients.get(path.removeprefix("/clients/"))
                if held is None:
                    return httpx.Response(404, json={"message": "not found"})
                if "type" in body:
                    return httpx.Response(400, json={"message": "'type' can not be changed"})
                for key, value in body.items():
                    merged = {**held.get(key, {}), **value} if isinstance(value, dict) else value
                    held[key] = merged
                return httpx.Response(200, json=held)
            if path == "/checks":
                self.created += 1
                found = f"chk_new{self.created}_{self.bed.nonce}"
                self.checks[found] = {"id": found, "status": "pending", **body}
                return httpx.Response(201, json={"id": found})
            return httpx.Response(200, json={"ok": True})
        return super()._handler(request)

    def client(self) -> ScreeningClient:
        async def no_sleep(seconds: float) -> None:
            del seconds

        return ScreeningClient(
            "test-key", transport=httpx.MockTransport(self._handler), sleep=no_sleep, max_retries=0
        )


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def console(maker: Maker, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    """A firm with one portal client, Graphene's operations tenant with its compliance role, an
    operator and a firm member, and ComplyCube stood in by ``Provider``."""
    monkeypatch.setattr(aml_router, "SessionLocal", maker)
    bed = await seed_bed(maker)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        ops_id = await make_tenant(s, "cco", bed.nonce)
        await s.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                "VALUES (:t, :s, 'Compliance', 'operations')"
            ),
            {"t": ops_id, "s": tasks.COMPLIANCE_ROLE_SLUG},
        )
        users: dict[str, str] = {}
        for tag, tenant_id in (("ops", ops_id), ("firm", bed.tenant_id)):
            workos = f"wos_{tag}_{bed.nonce}"
            await s.execute(
                text(
                    "INSERT INTO auth.users (id, tenant_id, email, first_name, last_name, "
                    "  workos_user_id) VALUES (:id, :t, :email, 'Olive', 'Operator', :w)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "t": tenant_id,
                    "email": f"{tag}-{bed.nonce}@aml.test",
                    "w": workos,
                },
            )
            users[tag] = workos
    bed.extra_tenants.append(ops_id)

    async def ops_tenant(session: AsyncSession) -> int:
        del session
        return ops_id

    monkeypatch.setattr(tasks, "operations_tenant_id", ops_tenant)
    provider = Provider(bed)
    monkeypatch.setattr(aml_router, "require_client", provider.client)
    # The identity clear and the identity images are shared decisions, which reach ComplyCube
    # through their own handle.
    monkeypatch.setattr(decisions, "require_client", provider.client)
    tenant_uid = (
        await fetch(maker, "SELECT uid FROM auth.tenants WHERE id = :t", {"t": bed.tenant_id})
    )[0]["uid"]
    yield {
        "bed": bed,
        "ops": ops_id,
        "ops_user": users["ops"],
        "firm_user": users["firm"],
        "provider": provider,
        "tenant_uid": tenant_uid,
    }
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("DELETE FROM compliance.client_risk WHERE tenant_id = :t"), {"t": bed.tenant_id}
        )
        await s.execute(
            text("DELETE FROM auth.users WHERE tenant_id = ANY(:ids)"),
            {"ids": [ops_id, bed.tenant_id]},
        )
    await bed.cleanup()


def _principal(workos_user: str, permissions: list[str]) -> Principal:
    return Principal(
        user_id=workos_user, organisation_id="org_test", role="member", permissions=permissions
    )


@asynccontextmanager
async def _ctx(
    principal: Principal, tenant: str | None = None
) -> AsyncIterator[aml_router.AmlContext]:
    """Drive ``aml_session`` as FastAPI does: commit when the handler returns, roll back when it
    raises."""
    generator = aml_router.aml_session(tenant=tenant, principal=principal)
    ctx = await anext(generator)
    try:
        yield ctx
    except BaseException:
        await generator.aclose()
        raise
    with pytest.raises(StopAsyncIteration):
        await anext(generator)


def _ops(console: dict[str, Any], permissions: list[str] | None = None) -> Principal:
    return _principal(console["ops_user"], ALL if permissions is None else permissions)


async def _contact(bed: Bed, tag: str) -> tuple[int, str, int]:
    """Another client of the bed's firm with its own compliance client.

    Returns ``(contact id, contact uid, compliance client id)``.
    """
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        uid = f"c{tag}{bed.nonce}"
        contact_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, name, type, service_type) "
                        "VALUES (:t, :u, :n, 'individual', 'advised') RETURNING id"
                    ),
                    {"t": bed.tenant_id, "u": uid, "n": f"Client {tag}"},
                )
            ).scalar_one()
        )
        client_pk = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO compliance.clients "
                        "  (tenant_id, contact_id, provider_client_id) "
                        "VALUES (:t, :c, :p) RETURNING id"
                    ),
                    {"t": bed.tenant_id, "c": contact_id, "p": f"cli_{tag}_{bed.nonce}"},
                )
            ).scalar_one()
        )
    return contact_id, uid, client_pk


async def _check(
    bed: Bed,
    client_pk: int,
    tag: str,
    *,
    kind: str = "aml",
    status: str = "refer",
    origin: str = "portal",
    kyc_check_id: int | None = None,
    decided: bool = False,
    stale: bool = False,
    matches: list[str | None] | None = None,
) -> str:
    """A check row on ``client_pk`` with one match per entry (its decision, or None)."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            await s.execute(
                text(
                    "INSERT INTO compliance.checks (tenant_id, client_id, kind, status, "
                    "  provider_check_id, origin, kyc_check_id, decided_at, stale_at) "
                    "VALUES (:t, :c, :k, :st, :p, :o, :kyc, "
                    "  CASE WHEN :decided THEN now() END, CASE WHEN :stale THEN now() END) "
                    "RETURNING id, uid"
                ),
                {
                    "t": bed.tenant_id,
                    "c": client_pk,
                    "k": kind,
                    "st": status,
                    "p": bed.ref(f"chk_{tag}"),
                    "o": origin,
                    "kyc": kyc_check_id,
                    "decided": decided,
                    "stale": stale,
                },
            )
        ).one()
        for i, decision in enumerate(matches or []):
            await s.execute(
                text(
                    "INSERT INTO compliance.matches (tenant_id, check_id, provider_match_id, "
                    "  name, match_types, score, decision) "
                    "VALUES (:t, :c, :p, :n, ARRAY['sanction'], :score, :d)"
                ),
                {
                    "t": bed.tenant_id,
                    "c": int(row.id),
                    "p": f"m{i}",
                    "n": f"Match {i}",
                    "score": 90 - i,
                    "d": decision,
                },
            )
    return str(row.uid)


async def _kyc_status(bed: Bed, kyc_id: int) -> str:
    rows = await fetch(bed.maker, "SELECT status FROM crm.kyc_checks WHERE id = :k", {"k": kyc_id})
    return str(rows[0]["status"])


async def _check_state(bed: Bed, uid: str) -> dict[str, Any]:
    rows = await fetch(
        bed.maker,
        "SELECT status, decided_by, decided_at, decision_comment, kyc_check_id, attempt, origin, "
        "  monitoring FROM compliance.checks WHERE uid = :u",
        {"u": uid},
    )
    return rows[0]


async def _ops_tasks(console: dict[str, Any]) -> list[dict[str, Any]]:
    return await fetch(
        console["bed"].maker,
        "SELECT title, status, source_ref FROM tasks.tasks WHERE tenant_id = :t ORDER BY id",
        {"t": console["ops"]},
    )


async def _raise_task(console: dict[str, Any], ref: str) -> None:
    async with console["bed"].maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await tasks.raise_screening_task(
            s,
            contact_id=console["bed"].contact_id,
            tenant_id=console["bed"].tenant_id,
            title="Run AML screening for Ada Lovelace",
            description="Screen them.",
            source_ref=ref,
        )


# ---- the gate -----------------------------------------------------------------------------------


async def test_a_firm_principal_cannot_reach_the_console(console: dict[str, Any]) -> None:
    """A firm's own staff have no route in, however senior: `aml:read` is registered against the
    operations portal, and every endpoint asserts it rather than trusting that."""
    principal = _principal(
        console["firm_user"], ["contacts:read", "contacts:read_all", "screening:read"]
    )
    async with _ctx(principal) as ctx:
        with pytest.raises(HTTPException) as raised:
            await aml_router.list_clients(ctx=ctx)
        assert raised.value.status_code == 403
        with pytest.raises(HTTPException) as raised:
            await aml_router.get_client("anything", ctx=ctx)
        assert raised.value.status_code == 403
        with pytest.raises(HTTPException) as raised:
            await aml_router.get_check_client("anything", ctx=ctx)
        assert raised.value.status_code == 403
        with pytest.raises(HTTPException) as raised:
            await aml_router.get_check("anything", ctx=ctx)
        assert raised.value.status_code == 403
        with pytest.raises(HTTPException) as raised:
            await aml_router.run_screen(
                aml_router.ScreenRequest(contact_uid=console["bed"].contact_uid), ctx=ctx
            )
        assert raised.value.status_code == 403


async def test_reading_is_not_deciding(console: dict[str, Any]) -> None:
    bed = console["bed"]
    uid = await _check(bed, bed.client_pk, "gate", matches=[None])
    match_uid = (
        await fetch(
            bed.maker,
            "SELECT m.uid FROM compliance.matches m JOIN compliance.checks c ON c.id = m.check_id "
            "WHERE c.uid = :u",
            {"u": uid},
        )
    )[0]["uid"]
    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console, ["aml:read", "aml:write"])) as ctx:
            await aml_router.decide_match(
                uid,
                match_uid,
                aml_router.MatchDecisionRequest(decision="dismissed", comment="Different person"),
                ctx=ctx,
            )
    assert raised.value.status_code == 403


async def test_writes_are_503_without_a_complycube_key(
    console: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.complycube import client as client_module

    monkeypatch.setattr(aml_router, "require_client", client_module.require_client)
    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console)) as ctx:
            await aml_router.run_screen(
                aml_router.ScreenRequest(contact_uid=console["bed"].contact_uid), ctx=ctx
            )
    assert raised.value.status_code == 503
    assert raised.value.detail == "ComplyCube is not configured."


# ---- the client list ----------------------------------------------------------------------------


async def _bare_contact(bed: Bed, tag: str, *, awaiting: bool = True) -> tuple[int, str]:
    """A client of the bed's firm with no compliance client yet, due a first AML screen.

    Returns ``(contact id, contact uid)``.
    """
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        uid = f"c{tag}{bed.nonce}"
        contact_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, name, type, service_type) "
                        "VALUES (:t, :u, :n, 'individual', 'advised') RETURNING id"
                    ),
                    {"t": bed.tenant_id, "u": uid, "n": f"Client {tag}"},
                )
            ).scalar_one()
        )
        if awaiting:
            await s.execute(
                text(
                    "INSERT INTO crm.kyc_checks (tenant_id, contact_id, kind, status, provider) "
                    "VALUES (:t, :c, 'aml', 'pending', 'complycube')"
                ),
                {"t": bed.tenant_id, "c": contact_id},
            )
    return contact_id, uid


async def _client_key(bed: Bed, client_pk: int) -> str:
    rows = await fetch(
        bed.maker, "SELECT uid FROM compliance.clients WHERE id = :c", {"c": client_pk}
    )
    return str(rows[0]["uid"])


async def _created_day(bed: Bed, contact_uid: str) -> str:
    """The day a contact was created, as ``client_since`` reads it."""
    rows = await fetch(
        bed.maker, "SELECT created_at FROM crm.contacts WHERE uid = :u", {"u": contact_uid}
    )
    return str(rows[0]["created_at"].date().isoformat())


async def _risk(bed: Bed, client_pk: int, overall: str, profile: dict[str, Any]) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "INSERT INTO compliance.client_risk "
                "  (client_id, tenant_id, overall, profile, provider_updated_at) "
                "VALUES (:c, :t, :o, CAST(:p AS jsonb), '2026-09-30T10:00:00Z')"
            ),
            {"c": client_pk, "t": bed.tenant_id, "o": overall, "p": json.dumps(profile)},
        )


async def _clients(console: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    async with _ctx(_ops(console), tenant=console["tenant_uid"]) as ctx:
        return await aml_router.list_clients(ctx=ctx, **kwargs)


async def _row(console: dict[str, Any], contact_uid: str) -> dict[str, Any]:
    """The one list row for a firm client."""
    (row,) = (await _clients(console, contact=contact_uid))["items"]
    return row


async def _client_page(console: dict[str, Any], key: str) -> dict[str, Any]:
    async with _ctx(_ops(console)) as ctx:
        return await aml_router.get_client(key, ctx=ctx)


async def test_clients_that_need_a_human_come_first(console: dict[str, Any]) -> None:
    bed = console["bed"]
    await _check(bed, bed.client_pk, "ada", kind="identity", status="clear")
    _c, open_uid, open_client = await _contact(bed, "open")
    open_check = await _check(bed, open_client, "open", matches=[None, "dismissed"])
    # A portal check held on a confirmed match needs a human; one allowed to continue does not.
    _c, held_uid, held_client = await _contact(bed, "held")
    await _check(bed, held_client, "held", matches=["confirmed", "dismissed"])
    _c, settled_uid, settled_client = await _contact(bed, "settled")
    await _check(bed, settled_client, "settled", decided=True, matches=["confirmed", "dismissed"])
    _c, ident_uid, ident_client = await _contact(bed, "ident")
    await _check(bed, ident_client, "ident", kind="identity")
    _c, failed_uid, failed_client = await _contact(bed, "failed")
    await _check(bed, failed_client, "failed", status="failed")
    _c, rerun_uid, rerun_client = await _contact(bed, "rerun")
    await _check(bed, rerun_client, "rerun", status="failed", decided=True)
    await _check(bed, rerun_client, "rerun2", status="pending")
    _c, stale_uid, stale_client = await _contact(bed, "stale")
    stale_check = await _check(bed, stale_client, "stale", status="clear", stale=True)
    _c, replaced_uid, replaced_client = await _contact(bed, "replaced")
    await _check(bed, replaced_client, "old", status="clear", stale=True)
    await _check(bed, replaced_client, "new", status="pending")
    _c, asked_uid, asked_client = await _contact(bed, "asked")
    await _check(bed, asked_client, "asked", kind="identity", decided=True)
    _c, stuck_uid, stuck_client = await _contact(bed, "stuck")
    stuck = await _check(bed, stuck_client, "stuck", status="pending")
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "UPDATE compliance.checks SET created_at = now() - interval '8 days' WHERE uid = :u"
            ),
            {"u": stuck},
        )
    _c, bare_uid = await _bare_contact(bed, "bare")

    listed = await _clients(console)
    assert listed["total"] == 12
    assert listed["page_size"] == aml_router.PAGE_SIZE
    by_contact = {row["contact_uid"]: row for row in listed["items"]}
    assert {uid for uid, row in by_contact.items() if row["needs_attention"]} == {
        open_uid,
        held_uid,
        ident_uid,
        failed_uid,
        stale_uid,
        stuck_uid,
        bare_uid,
    }
    # Needs a human first, then the most recently updated.
    flags = [row["needs_attention"] for row in listed["items"]]
    assert flags == sorted(flags, reverse=True)
    for group in (True, False):
        stamps = [row["updated_at"] for row in listed["items"] if row["needs_attention"] is group]
        assert stamps == sorted(stamps, reverse=True)

    # A portal client due a first screen with no compliance client yet: keyed by the contact.
    bare = by_contact[bare_uid]
    assert bare["key"] == bare_uid
    assert bare["aml"] == {
        "uid": None,
        "status": "awaiting_screen",
        "decided": False,
        "monitoring": False,
        "updated_at": bare["updated_at"],
        "queued": False,
    }
    assert bare["identity"] is None
    assert bare["origin"] == "portal"
    assert bare["client_name"] == "Client bare"

    # The latest check of each kind, and what it says.
    assert by_contact[stale_uid]["stale"] is True
    assert by_contact[replaced_uid]["stale"] is False
    assert by_contact[replaced_uid]["aml"]["status"] == "pending"
    assert by_contact[settled_uid]["aml"]["decided"] is True
    assert by_contact[held_uid]["aml"]["decided"] is False
    assert by_contact[asked_uid]["identity"]["status"] == "refer"
    assert by_contact[asked_uid]["identity"]["decided"] is True
    assert by_contact[rerun_uid]["aml"]["status"] == "pending"
    ada = by_contact[bed.contact_uid]
    # A client with a compliance client is still keyed by its contact.
    assert ada["key"] == bed.contact_uid
    assert ada["identity"]["status"] == "clear"
    assert ada["aml"] is None
    assert ada["tenant_uid"] == console["tenant_uid"]
    assert ada["tenant_icon_url"] is None
    assert set(ada) == {
        "key",
        "client_name",
        "contact_uid",
        "tenant_uid",
        "tenant_name",
        "tenant_icon_url",
        "origin",
        "identity",
        "aml",
        "stale",
        "risk",
        "needs_attention",
        "updated_at",
    }

    # The client page reads the same rule as the list, and says why.
    reasons = {
        open_uid: {"open_hits"},
        held_uid: {"held"},
        ident_uid: {"identity_refer"},
        failed_uid: {"screen_failed"},
        stale_uid: {"screen_stale"},
        stuck_uid: {"screen_no_answer"},
        bare_uid: {"screen_due"},
    }
    pages = {uid: await _client_page(console, row["key"]) for uid, row in by_contact.items()}
    for uid, page in pages.items():
        needed = by_contact[uid]["needs_attention"]
        assert page["needs_attention"] is needed, uid
        assert {reason["code"] for reason in page["reasons"]} == reasons.get(uid, set()), uid
        # A client needs attention exactly when one of its steps is open.
        assert any(step["state"] == "open" for step in page["steps"]) is needed, uid
        assert (page["waiting_since"] is not None) is needed, uid
        assert (page["age"] is not None) is needed, uid
    (hits,) = pages[open_uid]["reasons"]
    assert (hits["count"], hits["check_uid"]) == (1, open_check)
    (stale_reason,) = pages[stale_uid]["reasons"]
    assert stale_reason["check_uid"] == stale_check
    assert pages[bare_uid]["reasons"][0]["check_uid"] is None
    assert pages[held_uid]["steps"][3] == {
        "key": "hits",
        "label": "Hits reviewed",
        "state": "open",
        "count": 1,
        "hint": "Held on a confirmed match",
    }


async def test_a_monitoring_alert_labels_open_hits(console: dict[str, Any]) -> None:
    bed = console["bed"]
    _c, alert_uid, alert_client = await _contact(bed, "alert")
    alerted = await _check(bed, alert_client, "alert", matches=[None])
    _c, done_uid, done_client = await _contact(bed, "done")
    settled = await _check(bed, done_client, "done", decided=True, matches=["dismissed"])
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("UPDATE compliance.checks SET alerted_at = now() WHERE uid = ANY(:u)"),
            {"u": [alerted, settled]},
        )

    page = await _client_page(console, alert_uid)
    reasons = {reason["code"]: reason for reason in page["reasons"]}
    assert set(reasons) == {"monitoring_alert", "open_hits"}
    assert reasons["monitoring_alert"]["check_uid"] == alerted
    assert {step["key"]: step["state"] for step in page["steps"]}["monitoring"] == "open"
    # An alert on a check nobody needs to act on any more is not work.
    settled_page = await _client_page(console, done_uid)
    assert settled_page["reasons"] == []
    assert settled_page["needs_attention"] is False


async def test_the_list_carries_complycubes_risk_level(console: dict[str, Any]) -> None:
    bed = console["bed"]
    _c, high_uid, high_client = await _contact(bed, "high")
    await _check(bed, high_client, "high", status="clear")
    await _risk(bed, high_client, "high", {"overall": "high"})
    _c, read_uid, read_client = await _contact(bed, "read")
    await _check(bed, read_client, "read", status="clear")
    # Stored `not_set` while the profile names its level `overallRisk`.
    await _risk(bed, read_client, "not_set", {"overallRisk": "medium"})
    _c, none_uid, none_client = await _contact(bed, "none")
    await _check(bed, none_client, "none", status="clear")

    assert (await _row(console, high_uid))["risk"] == "high"
    assert (await _row(console, read_uid))["risk"] == "medium"
    assert (await _row(console, none_uid))["risk"] is None

    by_risk = await _clients(console, sort="risk", dir="desc")
    assert [row["contact_uid"] for row in by_risk["items"]][:2] == [high_uid, read_uid]


async def test_the_list_searches_names_narrows_to_one_client_and_sorts(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    await _check(bed, bed.client_pk, "mine", kind="identity", status="clear")
    _c, other_uid, other_client = await _contact(bed, "other")
    await _check(bed, other_client, "other", status="clear")
    await _bare_contact(bed, "waiting")

    named = await _clients(console, q="client other")
    assert [row["contact_uid"] for row in named["items"]] == [other_uid]
    assert named["total"] == 1
    # A name with a LIKE wildcard in it matches only itself.
    assert (await _clients(console, q="100%"))["items"] == []

    one = await _clients(console, contact=bed.contact_uid)
    assert [row["contact_uid"] for row in one["items"]] == [bed.contact_uid]

    ordered = await _clients(console, sort="client", dir="desc")
    names = [row["client_name"] for row in ordered["items"]]
    assert names == sorted(names, reverse=True)
    # An unknown sort falls back to the default order rather than erroring.
    assert (await _clients(console, sort="nonsense"))["total"] == 3


async def test_the_list_pages_at_the_page_size(
    console: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bed = console["bed"]
    monkeypatch.setattr(aml_router, "PAGE_SIZE", 2)
    for tag in ("p1", "p2", "p3"):
        _c, _u, client_pk = await _contact(bed, tag)
        await _check(bed, client_pk, tag, status="clear")
    first = await _clients(console, sort="client")
    second = await _clients(console, sort="client", page=2)
    assert first["total"] == second["total"] == 3
    assert first["page_size"] == 2
    assert len(first["items"]) == 2
    assert [row["client_name"] for row in [*first["items"], *second["items"]]] == [
        "Client p1",
        "Client p2",
        "Client p3",
    ]


async def test_the_list_is_cross_firm_and_narrows_to_one(console: dict[str, Any]) -> None:
    bed = console["bed"]
    await _check(bed, bed.client_pk, "x", status="pending")
    async with _ctx(_ops(console)) as ctx:
        wide = await aml_router.list_clients(ctx=ctx, contact=bed.contact_uid)
    assert [row["tenant_uid"] for row in wide["items"]] == [console["tenant_uid"]]
    async with _ctx(_ops(console), tenant=console["tenant_uid"]) as ctx:
        narrow = await aml_router.list_clients(ctx=ctx)
    assert {row["tenant_uid"] for row in narrow["items"]} == {console["tenant_uid"]}
    # Narrowed to another firm, this firm's client is not there.
    other_tenant = await _other_tenant(console)
    async with _ctx(_ops(console), tenant=other_tenant) as ctx:
        elsewhere = await aml_router.list_clients(ctx=ctx, contact=bed.contact_uid)
    assert elsewhere["items"] == []


async def _other_tenant(console: dict[str, Any]) -> str:
    rows = await fetch(
        console["bed"].maker, "SELECT uid FROM auth.tenants WHERE id = :t", {"t": console["ops"]}
    )
    return str(rows[0]["uid"])


async def _write(bed: Bed, statement: str, params: dict[str, Any]) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(text(statement), params)


# ---- screen -------------------------------------------------------------------------------------


async def test_a_screen_is_idempotent_and_closes_the_screen_task(console: dict[str, Any]) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]
    kyc_id = await insert_kyc_check(bed)
    await _raise_task(console, f"screen:{bed.contact_id}:{kyc_id}")
    # A bed client with no provider id yet: the screen finds or creates one first.
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("UPDATE compliance.clients SET provider_client_id = NULL WHERE id = :c"),
            {"c": bed.client_pk},
        )

    request = aml_router.ScreenRequest(contact_uid=bed.contact_uid)
    async with _ctx(_ops(console)) as ctx:
        first = await aml_router.run_screen(request, ctx=ctx)
    async with _ctx(_ops(console)) as ctx:
        second = await aml_router.run_screen(request, ctx=ctx)

    assert first == second
    assert provider.calls.count("POST /checks") == 1
    assert provider.calls.count("POST /clients") == 1
    screen_body = next(body for path, body in provider.writes if path == "/checks")
    # Ongoing monitoring is always on for a new screen.
    assert screen_body == {
        "clientId": f"cli_new_{bed.nonce}",
        "type": "extensive_screening_check",
        "enableMonitoring": True,
    }
    client_body = next(body for path, body in provider.writes if path == "/clients")
    assert client_body["externalId"] == bed.contact_uid
    assert client_body["metadata"] == bed_metadata(bed)
    state = await _check_state(bed, first["check_uid"])
    assert state["origin"] == "portal"
    assert state["kyc_check_id"] == kyc_id
    assert state["status"] == "pending"
    assert state["monitoring"] is True
    assert [row["status"] for row in await _ops_tasks(console)] == ["done"]
    # No longer due a screen: the client's AML reads as the new check.
    row = await _row(console, bed.contact_uid)
    assert row["aml"]["uid"] == first["check_uid"]
    assert row["aml"]["status"] == "pending"


async def test_a_console_screen_with_no_journey_is_the_clients_aml_record(
    console: dict[str, Any],
) -> None:
    """Screened from the console with no journey's AML step waiting: the screen opens the client's
    own pending AML kyc row, and its clear passes it, so the firm's record shows the screen."""
    bed = console["bed"]
    async with _ctx(_ops(console)) as ctx:
        started = await aml_router.run_screen(
            aml_router.ScreenRequest(contact_uid=bed.contact_uid), ctx=ctx
        )
    state = await _check_state(bed, started["check_uid"])
    assert state["kyc_check_id"] is not None
    kyc_id = int(state["kyc_check_id"])
    assert await _kyc_status(bed, kyc_id) == "pending"

    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        check_pk = (
            await s.execute(
                text("UPDATE compliance.checks SET status = 'clear' WHERE uid = :u RETURNING id"),
                {"u": started["check_uid"]},
            )
        ).scalar_one()
        record = await routing.load_check(s, int(check_pk))
        assert record is not None
        await routing.route_result(s, record, "pending", "clear")
    assert await _kyc_status(bed, kyc_id) == "passed"


async def _run_queued_screen(console: dict[str, Any], kyc_id: int) -> None:
    """What the worker does with the ``aml.screen.run`` a journey's AML step queued."""
    bed = console["bed"]
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await screen.handle(
            s, bed.tenant_id, {"contact_id": bed.contact_id, "kyc_check_id": kyc_id}
        )


async def test_a_journeys_screen_runs_by_itself_once_and_closes_the_task(
    console: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]
    monkeypatch.setattr(screen, "require_client", provider.client)
    kyc_id = await insert_kyc_check(bed)
    await _raise_task(console, f"screen:{bed.contact_id}:{kyc_id}")

    await _run_queued_screen(console, kyc_id)
    await _run_queued_screen(console, kyc_id)  # a redelivery pays for nothing

    assert provider.calls.count("POST /checks") == 1
    row = await _row(console, bed.contact_uid)
    state = await _check_state(bed, row["aml"]["uid"])
    assert state["origin"] == "portal"
    assert state["kyc_check_id"] == kyc_id
    assert state["status"] == "pending"
    assert state["monitoring"] is True
    assert (
        next(body for path, body in provider.writes if path == "/checks")["enableMonitoring"]
        is True
    )
    assert [task["status"] for task in await _ops_tasks(console)] == ["done"]


async def test_a_journeys_screen_that_cannot_run_asks_operations(
    console: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bed = console["bed"]

    def unconfigured() -> ScreeningClient:
        raise ComplyCubeNotConfigured()

    monkeypatch.setattr(screen, "require_client", unconfigured)
    kyc_id = await insert_kyc_check(bed)

    await _run_queued_screen(console, kyc_id)

    [task] = await _ops_tasks(console)
    assert task["status"] == "open"


@pytest.mark.parametrize("step", ["screen", "client"])
async def test_a_journeys_screen_retries_on_a_provider_hiccup_and_hands_over_on_a_refusal(
    console: dict[str, Any], monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """Whichever call ComplyCube fails: starting the screen, or fetching the bound client to bring
    it up to date first."""
    bed = console["bed"]
    provider: Provider = console["provider"]
    monkeypatch.setattr(screen, "require_client", provider.client)
    kyc_id = await insert_kyc_check(bed)
    provider.status_paths = {
        "/checks" if step == "screen" else f"/clients/{bed.provider_client_id}"
    }

    provider.status_override = 503
    with pytest.raises(httpx.HTTPStatusError):
        await _run_queued_screen(console, kyc_id)
    assert await _ops_tasks(console) == []

    provider.status_override = 400
    await _run_queued_screen(console, kyc_id)
    [task] = await _ops_tasks(console)
    assert task["status"] == "open"


async def _screen_bed(console: dict[str, Any]) -> dict[str, Any]:
    async with _ctx(_ops(console)) as ctx:
        return await aml_router.run_screen(
            aml_router.ScreenRequest(contact_uid=console["bed"].contact_uid), ctx=ctx
        )


async def test_screening_a_bound_client_first_sends_complycube_their_current_details(
    console: dict[str, Any],
) -> None:
    """A surname corrected after the ComplyCube client was made reaches ComplyCube before the
    screen, so "Screen again" screens who the client is now. Only what changed is sent."""
    bed = console["bed"]
    provider: Provider = console["provider"]
    await _write(
        bed,
        "UPDATE crm.contacts SET first_name = 'Ada', surname = 'King' WHERE id = :c",
        {"c": bed.contact_id},
    )

    await _screen_bed(console)

    update = f"/clients/{bed.provider_client_id}"
    assert [body for path, body in provider.writes if path == update] == [
        {"personDetails": {"lastName": "King"}}
    ]
    assert provider.calls.index(f"POST {update}") < provider.calls.index("POST /checks")
    assert provider.clients[bed.provider_client_id]["personDetails"] == {
        "firstName": "Ada",
        "lastName": "King",
        "nationality": "GB",
    }
    # What ComplyCube now holds is recorded on the client row.
    (row,) = await fetch(
        bed.maker,
        "SELECT first_name, last_name, external_id FROM compliance.clients WHERE id = :c",
        {"c": bed.client_pk},
    )
    assert dict(row) == {"first_name": "Ada", "last_name": "King", "external_id": bed.contact_uid}


async def test_a_bound_client_complycube_already_holds_as_is_is_not_written(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]

    await _screen_bed(console)

    assert f"GET /clients/{bed.provider_client_id}" in provider.calls
    assert [path for path, _ in provider.writes] == ["/checks"]


async def test_a_bound_client_complycube_files_under_another_id_is_not_changed_or_screened(
    console: dict[str, Any],
) -> None:
    """The binding has gone wrong: stop, rather than overwrite or screen another person."""
    bed = console["bed"]
    provider: Provider = console["provider"]
    provider.clients[bed.provider_client_id]["externalId"] = "someone-else"
    await _write(
        bed,
        "UPDATE crm.contacts SET first_name = 'Ada', surname = 'King' WHERE id = :c",
        {"c": bed.contact_id},
    )

    with pytest.raises(HTTPException) as raised:
        await _screen_bed(console)

    assert raised.value.status_code == 409
    assert provider.writes == []
    assert provider.clients[bed.provider_client_id]["personDetails"]["lastName"] == "Lovelace"


async def test_a_bound_client_made_before_stamping_is_stamped_and_keeps_other_keys(
    console: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ComplyCube replaces metadata whole on an update, so ours goes over what it holds."""
    bed = console["bed"]
    provider: Provider = console["provider"]
    monkeypatch.setattr(settings, "app_stage", "staging")
    provider.clients[bed.provider_client_id]["metadata"] = {"ops_note": "kept"}

    await _screen_bed(console)

    update = f"/clients/{bed.provider_client_id}"
    assert [body for path, body in provider.writes if path == update] == [
        {"metadata": {"ops_note": "kept", "graphene_env": "staging", **bed_metadata(bed)}}
    ]


async def test_a_bound_client_stamped_by_another_deployment_is_not_changed_or_screened(
    console: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]
    monkeypatch.setattr(settings, "app_stage", "staging")
    provider.clients[bed.provider_client_id]["metadata"] = {"graphene_env": "dev"}

    with pytest.raises(HTTPException) as raised:
        await _screen_bed(console)

    assert raised.value.status_code == 409
    assert provider.writes == []


async def test_a_new_complycube_client_is_stamped_with_this_deployment(
    console: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]
    monkeypatch.setattr(settings, "app_stage", "staging")
    await _write(
        bed,
        "UPDATE compliance.clients SET provider_client_id = NULL WHERE id = :c",
        {"c": bed.client_pk},
    )

    await _screen_bed(console)

    client_body = next(body for path, body in provider.writes if path == "/clients")
    assert client_body["metadata"] == {"graphene_env": "staging", **bed_metadata(bed)}


async def test_complycube_refusing_the_new_details_stops_the_screen_for_a_person(
    console: dict[str, Any],
) -> None:
    """Only correcting the client record can fix refused details, and screening the old ones is the
    stale screen this exists to stop: the console says why, and nothing is screened."""
    bed = console["bed"]
    provider: Provider = console["provider"]
    await _write(
        bed,
        "UPDATE crm.contacts SET first_name = 'Ada', surname = 'King' WHERE id = :c",
        {"c": bed.contact_id},
    )
    provider.status_paths = {f"POST /clients/{bed.provider_client_id}"}
    provider.status_override = 422

    with pytest.raises(HTTPException) as raised:
        await _screen_bed(console)

    assert raised.value.status_code == 409
    assert "ComplyCube refused this client's details" in str(raised.value.detail)
    assert "POST /checks" not in provider.calls


async def test_a_partner_client_screened_from_the_console_stays_a_partner_one(
    console: dict[str, Any],
) -> None:
    """``graphene_origin`` is set when the client is made and never changed; a partner client's own
    keys survive too, because an update replaces metadata whole."""
    bed = console["bed"]
    provider: Provider = console["provider"]
    partner = {
        "graphene_origin": "partner",
        "graphene_client_uid": "partnerclnt1",
        "graphene_firm_reference": "CRM-42",
    }
    provider.clients[bed.provider_client_id]["metadata"] = partner

    await _screen_bed(console)

    update = f"/clients/{bed.provider_client_id}"
    (body,) = [body for path, body in provider.writes if path == update]
    assert body == {"metadata": {**bed_metadata(bed), **partner}}


async def test_a_client_found_by_its_contact_uid_is_reused_only_if_it_is_this_deployments(
    console: dict[str, Any],
) -> None:
    """ComplyCube does not keep ``externalId`` unique. A found client stamped with this deployment
    is ours from an earlier try: reused, and never written to. One carrying another deployment's
    name, or none, is not ours: a new client is made."""
    bed = console["bed"]
    provider: Provider = console["provider"]
    provider.searchable = True
    unbound = "UPDATE compliance.clients SET provider_client_id = NULL WHERE id = :c"

    await _write(bed, unbound, {"c": bed.client_pk})
    await _screen_bed(console)
    assert provider.calls.count("POST /clients") == 0
    assert not any(path.startswith("/clients/") for path, _ in provider.writes)
    assert next(body for path, body in provider.writes if path == "/checks")["clientId"] == (
        bed.provider_client_id
    )

    for stamp in ({"graphene_env": "elsewhere"}, {}):
        await _write(
            bed,
            "DELETE FROM compliance.checks WHERE client_id = :c",
            {"c": bed.client_pk},
        )
        await _write(bed, unbound, {"c": bed.client_pk})
        provider.clients.pop(f"cli_new_{bed.nonce}", None)
        provider.clients[bed.provider_client_id]["metadata"] = stamp
        provider.writes.clear()
        await _screen_bed(console)
        (made,) = [body for path, body in provider.writes if path == "/clients"]
        assert made["metadata"] == bed_metadata(bed)
        assert next(body for path, body in provider.writes if path == "/checks")["clientId"] == (
            f"cli_new_{bed.nonce}"
        )


async def test_a_rerun_of_a_portal_screen_sends_complycube_the_current_details_first(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]
    kyc_id = await insert_kyc_check(bed)
    uid = await _check(bed, bed.client_pk, "rerun", status="failed", kyc_check_id=kyc_id)
    await _write(
        bed,
        "UPDATE crm.contacts SET first_name = 'Ada', surname = 'King' WHERE id = :c",
        {"c": bed.contact_id},
    )

    async with _ctx(_ops(console)) as ctx:
        await aml_router.rerun_check(uid, ctx=ctx)

    update = f"POST /clients/{bed.provider_client_id}"
    assert provider.calls.index(update) < provider.calls.index("POST /checks")
    assert provider.clients[bed.provider_client_id]["personDetails"]["lastName"] == "King"


@pytest.mark.parametrize("origin", ["portal", "partner"])
@pytest.mark.parametrize("trouble", ["elsewhere", "not_ours"])
async def test_a_rerun_on_a_complycube_client_that_is_not_this_clients_screens_nothing(
    console: dict[str, Any], monkeypatch: pytest.MonkeyPatch, origin: str, trouble: str
) -> None:
    """Another deployment's stamp, or someone else's ``externalId``: 409, nothing written."""
    bed = console["bed"]
    provider: Provider = console["provider"]
    monkeypatch.setattr(settings, "app_stage", "staging")
    held = provider.clients[bed.provider_client_id]
    if trouble == "elsewhere":
        held["metadata"] = {"graphene_env": "dev"}
    else:
        held["externalId"] = "someone-else"
    kyc_id = await insert_kyc_check(bed) if origin == "portal" else None
    uid = await _check(
        bed, bed.client_pk, "nrerun", status="failed", origin=origin, kyc_check_id=kyc_id
    )

    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console)) as ctx:
            await aml_router.rerun_check(uid, ctx=ctx)

    assert raised.value.status_code == 409
    assert provider.writes == []


async def test_screening_an_unknown_client_is_404(console: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console)) as ctx:
            await aml_router.run_screen(
                aml_router.ScreenRequest(contact_uid="nobody-here"), ctx=ctx
            )
    assert raised.value.status_code == 404


# ---- match decisions ----------------------------------------------------------------------------


async def _match_uids(bed: Bed, check_uid: str) -> list[str]:
    rows = await fetch(
        bed.maker,
        "SELECT m.uid FROM compliance.matches m JOIN compliance.checks c ON c.id = m.check_id "
        "WHERE c.uid = :u ORDER BY m.provider_match_id",
        {"u": check_uid},
    )
    return [str(row["uid"]) for row in rows]


async def _decide(
    console: dict[str, Any], uid: str, match_uid: str, decision: str, comment: str = "Checked"
) -> dict[str, Any]:
    async with _ctx(_ops(console)) as ctx:
        return await aml_router.decide_match(
            uid,
            match_uid,
            aml_router.MatchDecisionRequest(decision=decision, comment=comment),  # type: ignore[arg-type]
            ctx=ctx,
        )


async def test_every_match_dismissed_settles_clear_and_passes_the_kyc_row(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]
    kyc_id = await insert_kyc_check(bed)
    uid = await _check(bed, bed.client_pk, "clear", kyc_check_id=kyc_id, matches=[None, None])
    first, second = await _match_uids(bed, uid)

    after_one = await _decide(console, uid, first, "dismissed", "Different date of birth")
    assert after_one["check"]["status"] == "refer"
    assert await _kyc_status(bed, kyc_id) == "pending"
    decided = next(m for m in after_one["matches"] if m["uid"] == first)
    assert decided["decision"] == "dismissed"
    assert decided["decided_by"] == "Olive Operator"
    assert decided["comment"] == "Different date of birth"

    settled = await _decide(console, uid, second, "dismissed")
    assert settled["check"]["status"] == "clear"
    assert settled["check"]["decided_by"] == "Olive Operator"
    assert settled["check"]["decided_at"] is not None
    assert await _kyc_status(bed, kyc_id) == "passed"
    # One validate per match, each a rejection. No check-level validate: ComplyCube clears a
    # screening check by itself once every match is rejected, and refuses one without a matchId.
    validated = [body for path, body in provider.writes if path.endswith("/validate")]
    assert [body.get("matchId") for body in validated] == ["m0", "m1"]
    assert {body["outcome"] for body in validated} == {"rejected"}
    # Settled: no match can be decided again.
    with pytest.raises(HTTPException) as raised:
        await _decide(console, uid, first, "confirmed")
    assert raised.value.status_code == 409


async def test_a_confirmed_match_holds_a_portal_check_and_its_matches_stay_changeable(
    console: dict[str, Any],
) -> None:
    """The last match decided with one confirmed: the portal check is HELD, not decided. The kyc
    row stays pending and the screening task stays open, so the client still needs an operator.
    While it is held, a match can still be changed; every one dismissed clears it."""
    bed = console["bed"]
    kyc_id = await insert_kyc_check(bed)
    uid = await _check(bed, bed.client_pk, "conf", kyc_check_id=kyc_id, matches=[None, None])
    await _raise_task(console, tasks.check_task_ref(uid))
    first, second = await _match_uids(bed, uid)
    await _decide(console, uid, first, "confirmed", "Same person: passport matches")
    held = await _decide(console, uid, second, "dismissed")
    assert held["check"]["status"] == "refer"
    assert held["check"]["decided_at"] is None
    assert held["check"]["decided_by"] is None
    assert held["check"]["decision_comment"] == "1 of 2 matches confirmed."
    assert await _kyc_status(bed, kyc_id) == "pending"
    assert [task["status"] for task in await _ops_tasks(console)] == ["open"]
    # Held, so the client still needs a human, on the list and on their page.
    row = await _row(console, bed.contact_uid)
    assert row["needs_attention"] is True
    assert row["aml"]["uid"] == uid
    assert row["aml"]["status"] == "refer"
    assert row["aml"]["decided"] is False
    assert (await _client_page(console, bed.contact_uid))["needs_attention"] is True

    # The operator got the confirmation wrong: it can be decided again while the check is held.
    reopened = await _decide(console, uid, first, "dismissed", "Re-checked: different person")
    assert reopened["check"]["status"] == "clear"
    assert reopened["check"]["decided_at"] is not None
    assert reopened["check"]["decision_comment"] == "All 2 matches dismissed."
    assert await _kyc_status(bed, kyc_id) == "passed"
    assert [task["status"] for task in await _ops_tasks(console)] == ["done"]
    assert (await _row(console, bed.contact_uid))["needs_attention"] is False


async def test_a_confirmed_match_on_a_partner_check_decides_it_at_once(
    console: dict[str, Any],
) -> None:
    """A partner check is not held: the last decision decides it as a refer, closes its task, and
    there is no journey to allow."""
    bed = console["bed"]
    uid = await _check(bed, bed.client_pk, "pconf", origin="partner", matches=[None, None])
    await _raise_task(console, tasks.check_task_ref(uid))
    first, second = await _match_uids(bed, uid)
    await _decide(console, uid, first, "confirmed", "Same person: passport matches")
    settled = await _decide(console, uid, second, "dismissed")
    assert settled["check"]["status"] == "refer"
    assert settled["check"]["decided_by"] == "Olive Operator"
    assert settled["check"]["decided_at"] is not None
    assert settled["check"]["decision_comment"] == "1 of 2 matches confirmed."
    assert [task["status"] for task in await _ops_tasks(console)] == ["done"]
    row = await _row(console, bed.contact_uid)
    assert row["needs_attention"] is False
    assert row["aml"]["decided"] is True
    with pytest.raises(HTTPException) as raised:
        await _allow(console, uid)
    assert raised.value.status_code == 409


# ---- allowing a held journey --------------------------------------------------------------------

#: The operator's reason for letting a held journey continue.
ALLOW_NOTE = "Known PEP. Enhanced due diligence done; the MLRO accepts the risk."


async def _allow(
    console: dict[str, Any],
    uid: str,
    comment: str | None = ALLOW_NOTE,
    permissions: list[str] | None = None,
) -> dict[str, Any]:
    async with _ctx(_ops(console, permissions)) as ctx:
        return await aml_router.allow_journey(
            uid, aml_router.AllowJourneyRequest(comment=comment), ctx=ctx
        )


async def _held(console: dict[str, Any], tag: str, kyc_id: int) -> str:
    """A portal AML check held on a confirmed match, decided through the console."""
    bed = console["bed"]
    uid = await _check(bed, bed.client_pk, tag, kyc_check_id=kyc_id, matches=[None, None])
    first, second = await _match_uids(bed, uid)
    await _decide(console, uid, first, "confirmed", "Same person: passport matches")
    await _decide(console, uid, second, "dismissed")
    return uid


async def test_allowing_a_held_journey_passes_the_kyc_row_and_freezes_the_matches(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]
    kyc_id = await insert_kyc_check(bed)
    uid = await _held(console, "allow", kyc_id)
    await _raise_task(console, tasks.check_task_ref(uid))
    validated = len(provider.writes)

    allowed = await _allow(console, uid)
    # The confirmed match stands on record: the check stays a refer, now decided.
    assert allowed["check"]["status"] == "refer"
    assert allowed["check"]["decided_by"] == "Olive Operator"
    assert allowed["check"]["decided_at"] is not None
    assert allowed["check"]["decision_comment"] == (
        f"1 of 2 matches confirmed. Allowed to continue: {ALLOW_NOTE}"
    )
    assert sorted(m["decision"] for m in allowed["matches"]) == ["confirmed", "dismissed"]
    assert await _kyc_status(bed, kyc_id) == "passed"
    assert [task["status"] for task in await _ops_tasks(console)] == ["done"]
    # Nothing goes to ComplyCube: the matches were validated there already.
    assert len(provider.writes) == validated
    row = await _row(console, bed.contact_uid)
    assert row["needs_attention"] is False
    assert row["aml"]["uid"] == uid
    assert row["aml"]["decided"] is True
    assert (await _client_page(console, bed.contact_uid))["needs_attention"] is False
    state = await _check_state(bed, uid)
    assert state["status"] == "refer"
    assert state["decided_at"] is not None

    # Allowed, so no match can change, and it cannot be allowed twice.
    for match_uid in await _match_uids(bed, uid):
        with pytest.raises(HTTPException) as raised:
            await _decide(console, uid, match_uid, "dismissed", "Changed my mind")
        assert raised.value.status_code == 409
        assert "allowed to continue" in str(raised.value.detail)
    with pytest.raises(HTTPException) as raised:
        await _allow(console, uid)
    assert raised.value.status_code == 409
    assert len(provider.writes) == validated


async def test_only_a_held_portal_aml_check_can_be_allowed(console: dict[str, Any]) -> None:
    """Every check that is not held is refused before anything is written."""
    bed = console["bed"]
    kyc_id = await insert_kyc_check(bed)
    refused = {
        "identity": await _check(bed, bed.client_pk, "al_ident", kind="identity"),
        "partner": await _check(
            bed, bed.client_pk, "al_partner", origin="partner", matches=["confirmed", "dismissed"]
        ),
        "an undecided match": await _check(
            bed, bed.client_pk, "al_open", kyc_check_id=kyc_id, matches=["confirmed", None]
        ),
        "nothing confirmed": await _check(
            bed, bed.client_pk, "al_none", kyc_check_id=kyc_id, matches=["dismissed", "dismissed"]
        ),
        "no matches": await _check(bed, bed.client_pk, "al_empty", kyc_check_id=kyc_id),
        "clear": await _check(
            bed,
            bed.client_pk,
            "al_clear",
            status="clear",
            kyc_check_id=kyc_id,
            decided=True,
            matches=["dismissed", "dismissed"],
        ),
        "already allowed": await _check(
            bed,
            bed.client_pk,
            "al_done",
            kyc_check_id=kyc_id,
            decided=True,
            matches=["confirmed", "dismissed"],
        ),
    }
    for why, uid in refused.items():
        before = await _check_state(bed, uid)
        with pytest.raises(HTTPException) as raised:
            await _allow(console, uid)
        assert raised.value.status_code == 409, why
        assert await _check_state(bed, uid) == before, why
    assert await _kyc_status(bed, kyc_id) == "pending"
    with pytest.raises(HTTPException) as raised:
        await _allow(console, "no-such-check")
    assert raised.value.status_code == 404


@pytest.mark.parametrize("comment", ["", "   ", None])
async def test_a_journey_can_be_allowed_without_a_comment(
    console: dict[str, Any], comment: str | None
) -> None:
    bed = console["bed"]
    kyc_id = await insert_kyc_check(bed)
    uid = await _held(console, "allownote", kyc_id)
    allowed = await _allow(console, uid, comment)
    assert allowed["check"]["status"] == "refer"
    assert allowed["check"]["decided_at"] is not None
    assert allowed["check"]["decision_comment"] == "1 of 2 matches confirmed. Allowed to continue."
    assert await _kyc_status(bed, kyc_id) == "passed"


async def test_allowing_a_journey_is_adjudication(console: dict[str, Any]) -> None:
    """``aml:write`` alone is not enough, and a firm principal has no route in at all."""
    bed = console["bed"]
    kyc_id = await insert_kyc_check(bed)
    uid = await _held(console, "allowperm", kyc_id)
    with pytest.raises(HTTPException) as raised:
        await _allow(console, uid, permissions=["aml:read", "aml:write"])
    assert raised.value.status_code == 403
    firm = _principal(
        console["firm_user"], ["contacts:read", "contacts:read_all", "screening:read"]
    )
    with pytest.raises(HTTPException) as raised:
        async with _ctx(firm) as ctx:
            await aml_router.allow_journey(
                uid, aml_router.AllowJourneyRequest(comment=ALLOW_NOTE), ctx=ctx
            )
    assert raised.value.status_code == 403
    assert (await _check_state(bed, uid))["decided_at"] is None
    assert await _kyc_status(bed, kyc_id) == "pending"


def test_allowing_a_journey_is_an_aml_event_that_keeps_the_note_out() -> None:
    """The middleware records the route as ``aml.journey_allowed``: the ``aml.`` prefix keeps it
    from every firm, and no subject is captured, so the operator's note never reaches the audit
    row. The check's history names the act."""
    from app.audit_actions import event_type_for, extract_subject
    from app.main import app
    from app.routers.firm import FIRM_EXCLUDED_EVENT_PREFIXES
    from tests.conftest import api_routes

    route = next(r for r in api_routes(app) if r.path == "/aml/checks/{uid}/allow")
    event_type = event_type_for(route)
    assert event_type == "aml.journey_allowed"
    assert event_type.startswith(FIRM_EXCLUDED_EVENT_PREFIXES)
    assert extract_subject(event_type, json.dumps({"comment": ALLOW_NOTE}).encode(), {}) is None
    assert aml_router._HISTORY_LABELS[event_type] == "Journey allowed to continue"


@pytest.mark.parametrize("comment", ["", "   ", None])
async def test_a_match_can_be_decided_without_a_comment(
    console: dict[str, Any], comment: str | None
) -> None:
    """A blank comment is no comment: the match is stored with none, and ComplyCube's validate
    is sent without one."""
    bed = console["bed"]
    uid = await _check(bed, bed.client_pk, "nocomment", matches=[None])
    (match_uid,) = await _match_uids(bed, uid)
    async with _ctx(_ops(console)) as ctx:
        detail = await aml_router.decide_match(
            uid,
            match_uid,
            aml_router.MatchDecisionRequest(decision="dismissed", comment=comment),
            ctx=ctx,
        )
    (match,) = detail["matches"]
    assert match["decision"] == "dismissed"
    assert match["decided_by"] == "Olive Operator"
    assert match["comment"] is None
    stored = await fetch(
        bed.maker, "SELECT comment FROM compliance.matches WHERE uid = :m", {"m": match_uid}
    )
    assert stored == [{"comment": None}]
    assert detail["check"]["status"] == "clear"
    validated = [body for path, body in console["provider"].writes if path.endswith("/validate")]
    assert validated == [{"outcome": "rejected", "matchId": "m0"}]


# ---- check decisions ----------------------------------------------------------------------------


async def _decide_check(console: dict[str, Any], uid: str, decision: str) -> dict[str, Any]:
    async with _ctx(_ops(console)) as ctx:
        return await aml_router.decide_check(
            uid,
            aml_router.CheckDecisionRequest(decision=decision, comment="Document checked"),  # type: ignore[arg-type]
            ctx=ctx,
        )


async def test_an_identity_refer_is_cleared_or_rejected(console: dict[str, Any]) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]
    kyc_id = await insert_kyc_check(bed, kind="identity")
    uid = await _check(bed, bed.client_pk, "idref", kind="identity", kyc_check_id=kyc_id)
    detail = await _decide_check(console, uid, "clear")
    assert detail["check"]["status"] == "clear"
    assert detail["check"]["decision_comment"] == "Document checked"
    assert await _kyc_status(bed, kyc_id) == "passed"
    assert [body for path, body in provider.writes if path.endswith("/validate")] == [
        {"outcome": "confirmed", "comment": "Document checked"}
    ]
    with pytest.raises(HTTPException) as raised:
        await _decide_check(console, uid, "rejected")
    assert raised.value.status_code == 409


async def test_an_identity_refer_an_operator_asked_new_photos_for_needs_nobody(
    console: dict[str, Any],
) -> None:
    """Asking the client for new photos decides the check (``decided_at``) and leaves its status
    ``refer``. Nobody needs to decide it again, so its client does not need attention, and the
    console refuses a decision or a second request on it."""
    bed = console["bed"]
    _c, held_uid, held_client = await _contact(bed, "held")
    await _check(bed, held_client, "held", kind="identity")
    _c, asked_uid, asked_client = await _contact(bed, "sentback")
    asked = await _check(bed, asked_client, "back", kind="identity", decided=True)
    assert (await _row(console, held_uid))["needs_attention"] is True
    asked_row = await _row(console, asked_uid)
    assert asked_row["needs_attention"] is False
    assert asked_row["identity"]["uid"] == asked
    with pytest.raises(HTTPException) as raised:
        await _decide_check(console, asked, "clear")
    assert raised.value.status_code == 409
    with pytest.raises(HTTPException) as raised:
        await _request_photos(console, asked)
    assert raised.value.status_code == 409


async def _request_photos(
    console: dict[str, Any], uid: str, permissions: list[str] | None = None
) -> dict[str, Any]:
    async with _ctx(_ops(console, permissions)) as ctx:
        return await aml_router.request_photos(
            uid,
            aml_router.PhotoRequest(photos=["selfie"], reason="face", comment="Selfie is dark"),
            ctx=ctx,
        )


async def test_only_an_undecided_portal_identity_refer_with_tries_left_asks_for_photos(
    console: dict[str, Any],
) -> None:
    """The full path, with the client's capture, is in ``test_identity_capture.py``. Here: every
    check it is not for is refused before anything is written."""
    bed = console["bed"]
    kyc_id = await insert_kyc_check(bed, kind="identity")
    refused = {
        "partner": await _check(
            bed, bed.client_pk, "rp_partner", kind="identity", origin="partner"
        ),
        "aml": await _check(bed, bed.client_pk, "rp_aml", matches=[None]),
        "pending": await _check(
            bed, bed.client_pk, "rp_pending", kind="identity", status="pending"
        ),
        "failed": await _check(bed, bed.client_pk, "rp_failed", kind="identity", status="failed"),
        "cleared": await _check(bed, bed.client_pk, "rp_clear", kind="identity", status="clear"),
        # A portal refer no step of the client's waits on any more (a journey that moved on).
        "no capture": await _check(
            bed, bed.client_pk, "rp_nocap", kind="identity", kyc_check_id=kyc_id
        ),
    }
    for why, uid in refused.items():
        with pytest.raises(HTTPException) as raised:
            await _request_photos(console, uid)
        assert raised.value.status_code == 409, why
        state = await _check_state(bed, uid)
        assert state["decided_at"] is None, why
    assert await _kyc_status(bed, kyc_id) == "pending"
    # Asking for photos is a decision on the check: `aml:write` alone is not enough.
    with pytest.raises(HTTPException) as raised:
        await _request_photos(console, refused["no capture"], ["aml:read", "aml:write"])
    assert raised.value.status_code == 403
    # Nothing goes to ComplyCube.
    assert console["provider"].writes == []


def test_a_photo_request_names_at_least_one_photo_and_a_known_reason() -> None:
    with pytest.raises(ValidationError):
        aml_router.PhotoRequest(photos=[], reason="none", comment="x")
    with pytest.raises(ValidationError):
        aml_router.PhotoRequest(photos=["passport"], reason="none", comment="x")  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        aml_router.PhotoRequest(photos=["selfie"], reason="looks fake", comment="x")  # type: ignore[arg-type]


def test_a_photo_request_is_an_aml_event_that_keeps_the_note_out() -> None:
    """The middleware records the route as ``aml.photos_requested``: the ``aml.`` prefix keeps it
    from every firm, and the subject is the photos and the reason code, never the operator's
    note."""
    from app.audit_actions import event_type_for, extract_subject
    from app.main import app
    from app.routers.firm import FIRM_EXCLUDED_EVENT_PREFIXES
    from tests.conftest import api_routes

    route = next(r for r in api_routes(app) if r.path == "/aml/checks/{uid}/request-photos")
    event_type = event_type_for(route)
    assert event_type == "aml.photos_requested"
    assert event_type.startswith(FIRM_EXCLUDED_EVENT_PREFIXES)
    body = json.dumps(
        {"photos": ["document", "selfie"], "reason": "glare", "comment": "Looks tampered"}
    ).encode()
    assert extract_subject(event_type, body, {}) == {
        "photos": ["document", "selfie"],
        "reason": "glare",
    }


def test_the_history_says_which_photos_and_what_the_client_was_told() -> None:
    detail = aml_router._photos_requested_detail(
        {"photos": ["document", "selfie"], "reason": "blurry"}
    )
    assert detail == (
        "Document and selfie · Told: The photo was not clear enough. Please retake it in good "
        "light."
    )
    assert aml_router._photos_requested_detail({"photos": ["selfie"], "reason": "none"}) == (
        "Selfie · No reason given"
    )
    assert aml_router._photos_requested_detail({}) is None


async def test_a_failed_identity_check_can_only_be_rejected(console: dict[str, Any]) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]
    kyc_id = await insert_kyc_check(bed, kind="identity")
    uid = await _check(
        bed, bed.client_pk, "idfail", kind="identity", status="failed", kyc_check_id=kyc_id
    )
    with pytest.raises(HTTPException) as raised:
        await _decide_check(console, uid, "clear")
    assert raised.value.status_code == 409
    detail = await _decide_check(console, uid, "rejected")
    assert detail["check"]["status"] == "rejected"
    assert await _kyc_status(bed, kyc_id) == "failed"
    # Nothing at ComplyCube to validate: it never ran.
    assert provider.writes == []


async def test_an_aml_check_is_never_decided_as_a_whole(console: dict[str, Any]) -> None:
    bed = console["bed"]
    uid = await _check(bed, bed.client_pk, "amlwhole", matches=[None])
    with pytest.raises(HTTPException) as raised:
        await _decide_check(console, uid, "clear")
    assert raised.value.status_code == 409


async def test_a_check_decision_needs_a_comment(console: dict[str, Any]) -> None:
    bed = console["bed"]
    uid = await _check(bed, bed.client_pk, "idnocomment", kind="identity")
    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console)) as ctx:
            await aml_router.decide_check(
                uid, aml_router.CheckDecisionRequest(decision="clear", comment=" "), ctx=ctx
            )
    assert raised.value.status_code == 422


# ---- run again ----------------------------------------------------------------------------------


@pytest.mark.parametrize("origin", ["portal", "partner"])
async def test_a_failed_screen_is_run_again(console: dict[str, Any], origin: str) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]
    kyc_id = await insert_kyc_check(bed) if origin == "portal" else None
    uid = await _check(
        bed, bed.client_pk, "fail", status="failed", origin=origin, kyc_check_id=kyc_id
    )
    await _raise_task(console, f"{tasks.check_task_ref(uid)}:failed")

    async with _ctx(_ops(console)) as ctx:
        out = await aml_router.rerun_check(uid, ctx=ctx)

    new = await _check_state(bed, out["check_uid"])
    assert new["status"] == "pending"
    assert new["origin"] == origin
    assert new["kyc_check_id"] == kyc_id
    # A technical failure is not one of the client's tries.
    assert new["attempt"] == 1
    old = await _check_state(bed, uid)
    assert old["status"] == "failed"
    assert old["decided_by"] == "Olive Operator"
    assert old["decision_comment"] == f"Run again as {out['check_uid']}"
    assert [row["status"] for row in await _ops_tasks(console)] == ["done"]
    # Monitoring is always on for the new screen, whatever the failed check had.
    assert old["monitoring"] is False
    assert new["monitoring"] is True
    assert next(body for path, body in provider.writes if path == "/checks") == {
        "clientId": bed.provider_client_id,
        "type": "extensive_screening_check",
        "enableMonitoring": True,
    }
    # It has been run again, so it is no longer waiting on anybody.
    row = await _row(console, bed.contact_uid)
    assert row["needs_attention"] is False
    assert row["aml"]["uid"] == out["check_uid"]
    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console)) as ctx:
            await aml_router.rerun_check(uid, ctx=ctx)
    assert raised.value.status_code == 409


async def test_only_a_failed_aml_check_is_run_again(console: dict[str, Any]) -> None:
    bed = console["bed"]
    for tag, kind, check_status in (("rc", "aml", "clear"), ("ri", "identity", "failed")):
        uid = await _check(bed, bed.client_pk, tag, kind=kind, status=check_status)
        with pytest.raises(HTTPException) as raised:
            async with _ctx(_ops(console)) as ctx:
                await aml_router.rerun_check(uid, ctx=ctx)
        assert raised.value.status_code == 409


# ---- monitoring ---------------------------------------------------------------------------------


async def test_monitoring_is_for_an_aml_check(console: dict[str, Any]) -> None:
    bed = console["bed"]
    provider: Provider = console["provider"]
    uid = await _check(bed, bed.client_pk, "mon", status="clear")
    async with _ctx(_ops(console)) as ctx:
        detail = await aml_router.set_monitoring(
            uid, aml_router.MonitoringRequest(enabled=True), ctx=ctx
        )
    assert detail["check"]["monitoring"] is True
    assert provider.writes == [(f"/checks/{bed.ref('chk_mon')}", {"enableMonitoring": True})]
    identity = await _check(bed, bed.client_pk, "monid", kind="identity", status="clear")
    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console)) as ctx:
            await aml_router.set_monitoring(
                identity, aml_router.MonitoringRequest(enabled=True), ctx=ctx
            )
    assert raised.value.status_code == 409


# ---- the check page -----------------------------------------------------------------------------


async def test_the_check_page_shape(console: dict[str, Any]) -> None:
    bed = console["bed"]
    uid = await _check(bed, bed.client_pk, "page", matches=[None, "dismissed"])
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "INSERT INTO compliance.events (tenant_id, provider_event_id, event_type, payload) "
                "VALUES (:t, :e, 'check.completed.attention', CAST(:p AS jsonb))"
            ),
            {
                "t": bed.tenant_id,
                "e": bed.ref("evt_page"),
                "p": json.dumps({"payload": {"id": bed.ref("chk_page")}}),
            },
        )
    # The console act the middleware records after the response: a later transaction.
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        ops_user = (
            await s.execute(
                text("SELECT id FROM auth.users WHERE workos_user_id = :w"),
                {"w": console["ops_user"]},
            )
        ).scalar_one()
        dismissed = (
            await s.execute(
                text(
                    "SELECT m.uid FROM compliance.matches m "
                    "JOIN compliance.checks c ON c.id = m.check_id "
                    "WHERE c.uid = :u AND m.decision = 'dismissed'"
                ),
                {"u": uid},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO audit.actions (event_type, tenant_id, target_tenant_id, actor_id, "
                "  actor_sub, method, route, status, path_params, metadata) "
                "VALUES ('aml.match_decided', :o, :t, :a, :sub, 'POST', "
                "  '/aml/checks/{uid}/matches/{match_uid}/decision', 200, "
                "  CAST(:pp AS jsonb), CAST(:md AS jsonb))"
            ),
            {
                "o": console["ops"],
                "t": bed.tenant_id,
                "a": ops_user,
                "sub": console["ops_user"],
                "pp": json.dumps({"uid": uid, "match_uid": dismissed}),
                "md": json.dumps({"subject": {"decision": "dismissed"}}),
            },
        )
    async with _ctx(_ops(console)) as ctx:
        detail = await aml_router.get_check(uid, ctx=ctx)
    assert set(detail) == {"check", "client", "matches", "identity", "history"}
    assert detail["check"]["uid"] == uid
    assert detail["check"]["provider_check_id"] == bed.ref("chk_page")
    assert detail["client"] == {
        "name": "Ada Lovelace",
        "type": "individual",
        "title": None,
        "dob": None,
        "nationality": "GB",
        "country": None,
        "email": None,
        "phone": None,
        "address": None,
        "gender": None,
        "occupation": None,
        "client_since": await _created_day(bed, bed.contact_uid),
        "adviser": None,
        "advisers": [],
        "contact_uid": bed.contact_uid,
        "tenant_uid": console["tenant_uid"],
        "tenant_name": f"Firm ccf {bed.nonce}",
        "tenant_icon_url": None,
    }
    # Undecided first.
    assert [m["decision"] for m in detail["matches"]] == [None, "dismissed"]
    assert detail["matches"][0]["score"] == 90.0
    assert detail["identity"] is None
    assert [(entry["action"], entry["actor"], entry["detail"]) for entry in detail["history"]] == [
        ("Check started", None, None),
        ("ComplyCube: check.completed.attention", None, None),
        ("Match decided", "Olive Operator", "Match 1: dismissed"),
    ]
    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console)) as ctx:
            await aml_router.get_check("no-such-check", ctx=ctx)
    assert raised.value.status_code == 404


async def _identity_check(bed: Bed, tag: str, **ids: str | None) -> str:
    """An identity check with its document row and a result. ``ids`` sets the check's ComplyCube
    ``provider_document_id`` / ``provider_live_photo_id``."""
    uid = await _check(bed, bed.client_pk, tag, kind="identity")
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        doc_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO compliance.documents (tenant_id, client_id, "
                        "  provider_document_id, document_type) "
                        "VALUES (:t, :c, :p, 'passport') RETURNING id"
                    ),
                    {"t": bed.tenant_id, "c": bed.client_pk, "p": bed.ref(f"doc_{tag}")},
                )
            ).scalar_one()
        )
        await s.execute(
            text(
                "UPDATE compliance.checks SET document_id = :d, result = CAST(:r AS jsonb), "
                "  provider_document_id = :pd, provider_live_photo_id = :pp WHERE uid = :u"
            ),
            {
                "d": doc_id,
                "u": uid,
                "pd": ids.get("provider_document_id"),
                "pp": ids.get("provider_live_photo_id"),
                "r": json.dumps(
                    {
                        "outcome": "attention",
                        "breakdown": {
                            "extractedData": {"documentDetails": {"documentNumber": "123"}},
                            "documentAuthenticity": {"outcome": "clear"},
                            "faceAuthentication": {
                                "outcome": "attention",
                                "breakdown": {"faceMatch": {"outcome": "attention"}},
                            },
                        },
                    }
                ),
            },
        )
    return uid


def _download(data: bytes, file_name: str) -> dict[str, str]:
    """A ComplyCube image download: the bytes in base64. It names a JPEG ``image/jpg``."""
    return {
        "fileName": file_name,
        "contentType": "image/jpg",
        "data": base64.b64encode(data).decode("ascii"),
    }


async def _views(bed: Bed, uid: str) -> list[dict[str, Any]]:
    """The check's ``aml.identity_images_viewed`` rows, oldest first."""
    return await fetch(
        bed.maker,
        "SELECT route, method, metadata FROM audit.actions "
        "WHERE event_type = 'aml.identity_images_viewed' AND path_params ->> 'uid' = :u "
        "ORDER BY id",
        {"u": uid},
    )


async def _image(
    console: dict[str, Any], uid: str, part: ImagePart, permissions: list[str] | None = None
) -> Response:
    async with _ctx(_ops(console, permissions)) as ctx:
        return await aml_router.get_check_image(uid, part, ctx=ctx)


async def test_a_client_read_without_images_lists_none_and_records_no_view(
    console: dict[str, Any],
) -> None:
    """The workbench reads the clients beside the one on screen without their images, and lists a
    check's images when it shows it: only that listing is a view."""
    bed = console["bed"]
    provider = console["provider"]
    doc, photo = bed.ref("pdoc_quiet"), bed.ref("photo_quiet")
    uid = await _identity_check(
        bed, "quiet", provider_document_id=doc, provider_live_photo_id=photo
    )
    provider.documents[doc] = {
        "id": doc,
        "type": "passport",
        "images": [{"id": "img_f", "documentSide": "front", "contentType": "image/jpg"}],
    }
    provider.photos[photo] = {"id": photo, "contentType": "image/png"}

    async with _ctx(_ops(console)) as ctx:
        quiet = await aml_router.get_client(bed.contact_uid, ctx=ctx, images=False)
    assert quiet["identity"]["check"]["uid"] == uid
    assert quiet["identity"]["identity"]["images"] is None
    assert quiet["identity"]["identity"]["images_missing"] is None
    assert quiet["identity"]["identity"]["document_type"] == "passport"
    assert await _views(bed, uid) == []

    async with _ctx(_ops(console)) as ctx:
        listed = await aml_router.list_check_images(uid, ctx=ctx)
    assert [image["part"] for image in listed["images"]] == ["front", "selfie"]
    assert listed["images_missing"] is None
    views = await _views(bed, uid)
    assert [(view["method"], view["route"]) for view in views] == [
        ("GET", "/aml/checks/{uid}/images")
    ]
    assert [as_json(view["metadata"])["subject"] for view in views] == [{"image_count": 2}]

    # Read ahead: listed without a view. Shown: the page records the view itself.
    async with _ctx(_ops(console)) as ctx:
        ahead = await aml_router.list_check_images(uid, ctx=ctx, record=False)
    assert [image["part"] for image in ahead["images"]] == ["front", "selfie"]
    assert len(await _views(bed, uid)) == 1
    async with _ctx(_ops(console)) as ctx:
        recorded = await aml_router.record_check_images_viewed(
            uid, aml_router.ImagesViewedRequest(image_count=2), ctx=ctx
        )
    assert recorded == {"recorded": True}
    with pytest.raises(ValidationError):
        aml_router.ImagesViewedRequest(image_count=0)

    # An AML screen has no images to list.
    aml = await _check(bed, bed.client_pk, "quiet_aml", matches=[None])
    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console)) as ctx:
            await aml_router.record_check_images_viewed(
                aml, aml_router.ImagesViewedRequest(image_count=1), ctx=ctx
            )
    assert raised.value.status_code == 404
    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console)) as ctx:
            await aml_router.list_check_images(aml, ctx=ctx)
    assert raised.value.status_code == 404
    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console, ["contacts:read"])) as ctx:
            await aml_router.list_check_images(uid, ctx=ctx)
    assert raised.value.status_code == 403


async def test_identity_images_are_listed_from_complycube_and_listing_them_is_one_logged_view(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    provider = console["provider"]
    doc, photo = bed.ref("pdoc_img"), bed.ref("photo_img")

    # A check that names nothing at ComplyCube lists nothing, and has nothing to serve.
    bare = await _identity_check(bed, "bare")
    async with _ctx(_ops(console)) as ctx:
        none_held = await aml_router.get_check(bare, ctx=ctx)
    identity = none_held["identity"]
    assert identity["document_type"] == "passport"
    assert identity["images"] == []
    # jsonb keeps its own key order, so the parts are compared in key order.
    assert sorted(identity["breakdown"], key=lambda part: part["key"]) == [
        {"key": "documentAuthenticity", "label": "Document authenticity", "outcome": "clear"},
        {"key": "faceAuthentication", "label": "Face authentication", "outcome": "attention"},
        {"key": "faceAuthentication.faceMatch", "label": "Face match", "outcome": "attention"},
    ]
    assert "123" not in json.dumps(identity)
    with pytest.raises(HTTPException) as raised:
        await _image(console, bare, "front")
    assert raised.value.status_code == 404
    assert await _views(bed, bare) == []

    # The images ComplyCube holds are listed from its own record of the document and the live
    # photo: which part, its label and its type (a side may be a PDF scan), and never a link.
    uid = await _identity_check(bed, "img", provider_document_id=doc, provider_live_photo_id=photo)
    provider.documents[doc] = {
        "id": doc,
        "type": "passport",
        "images": [
            {"id": "img_b", "documentSide": "back", "contentType": "application/pdf"},
            {"id": "img_f", "documentSide": "front", "contentType": "image/jpg"},
        ],
    }
    provider.photos[photo] = {"id": photo, "contentType": "image/png"}
    async with _ctx(_ops(console)) as ctx:
        listed = await aml_router.get_check(uid, ctx=ctx)
    assert listed["identity"]["images"] == [
        {
            "part": "front",
            "label": "ID front",
            "content_type": "image/jpeg",
            "source": "complycube",
        },
        {
            "part": "back",
            "label": "ID back",
            "content_type": "application/pdf",
            "source": "complycube",
        },
        {"part": "selfie", "label": "Selfie", "content_type": "image/png", "source": "complycube"},
    ]
    # Listing reads no image. Showing the check with its images is the one view recorded: how many
    # images it listed, and no part.
    assert not [call for call in provider.calls if "/download" in call]
    views = await _views(bed, uid)
    assert [(view["method"], view["route"]) for view in views] == [("GET", "/aml/checks/{uid}")]
    assert [as_json(view["metadata"])["subject"] for view in views] == [{"image_count": 3}]

    # Each image is read from ComplyCube when it is shown and never cached. Serving one is not
    # another view: the listing recorded it. The bytes reach no log.
    provider.downloads[f"/documents/{doc}/download/front"] = _download(JPEG, "front.jpg")
    provider.downloads[f"/livePhotos/{photo}/download"] = _download(PNG, "selfie.png")
    front = await _image(console, uid, "front")
    assert front.body == JPEG
    assert front.media_type == "image/jpeg"
    assert front.headers["cache-control"] == "private, no-store, max-age=0"
    assert front.headers["content-disposition"] == 'inline; filename="front.jpeg"'
    selfie = await _image(console, uid, "selfie")
    assert selfie.body == PNG
    assert selfie.media_type == "image/png"
    assert len(await _views(bed, uid)) == 1
    logged = json.dumps([as_json(view["metadata"]) for view in await _views(bed, uid)])
    assert base64.b64encode(JPEG).decode("ascii") not in logged
    assert base64.b64encode(PNG).decode("ascii") not in logged

    # Showing the check again is another view. Its history lists each view, and names no image.
    async with _ctx(_ops(console)) as ctx:
        again = await aml_router.get_check(uid, ctx=ctx)
    assert len(await _views(bed, uid)) == 2
    assert [
        entry["detail"] for entry in again["history"] if entry["action"] == "Identity images viewed"
    ] == [None, None]

    # An image ComplyCube does not hold is 404, and not a view.
    with pytest.raises(HTTPException) as raised:
        await _image(console, uid, "back")
    assert raised.value.status_code == 404
    assert len(await _views(bed, uid)) == 2

    # ComplyCube not answering is 502, and not a view.
    provider.status_override = 500
    with pytest.raises(HTTPException) as raised:
        await _image(console, uid, "front")
    assert raised.value.status_code == 502
    provider.status_override = None
    assert len(await _views(bed, uid)) == 2


async def test_any_identity_check_serves_its_images_not_only_one_held_for_review(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    provider = console["provider"]
    doc = bed.ref("pdoc_partner")
    uid = await _identity_check(bed, "partner", provider_document_id=doc)
    await _write(
        bed,
        "UPDATE compliance.checks SET origin = 'partner', status = 'clear', decided_at = now() "
        "WHERE uid = :u",
        {"u": uid},
    )
    provider.downloads[f"/documents/{doc}/download/front"] = _download(JPEG, "front.jpg")
    served = await _image(console, uid, "front")
    assert served.body == JPEG
    # Serving an image is not a view: the check page listing it is.
    assert await _views(bed, uid) == []


async def test_an_identity_image_needs_aml_read_and_an_identity_check(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    provider = console["provider"]
    doc = bed.ref("pdoc_gate")
    uid = await _identity_check(bed, "gate", provider_document_id=doc)
    provider.downloads[f"/documents/{doc}/download/front"] = _download(JPEG, "front.jpg")
    with pytest.raises(HTTPException) as raised:
        await _image(console, uid, "front", ["screening:read"])
    assert raised.value.status_code == 403
    screen_uid = await _check(bed, bed.client_pk, "gate_aml")
    for unknown in (screen_uid, "no-such-check"):
        with pytest.raises(HTTPException) as raised:
            await _image(console, unknown, "front")
        assert raised.value.status_code == 404
    assert await _views(bed, uid) == []


def test_the_identity_breakdown_tolerates_any_shape() -> None:
    assert aml_router.identity_breakdown(None) == []
    assert aml_router.identity_breakdown({"breakdown": "nope"}) == []
    assert aml_router.identity_breakdown(
        {"breakdown": [{"name": "dataValidation", "outcome": "clear"}, "junk", {"x": 1}]}
    ) == [{"key": "dataValidation", "label": "Data validation", "outcome": "clear"}]
    assert aml_router.identity_breakdown(json.dumps({"breakdown": {"face_match": "clear"}})) == [
        {"key": "face_match", "label": "Face match", "outcome": "clear"}
    ]


def test_a_confirmed_match_and_a_cleared_identity_send_confirmed() -> None:
    """ComplyCube's validate takes confirmed, rejected or attention — never clear."""
    assert aml_router.MATCH_OUTCOMES == {"confirmed": "confirmed", "dismissed": "rejected"}
    assert aml_router.CHECK_OUTCOMES == {"clear": "confirmed", "rejected": "rejected"}
    allowed = {"confirmed", "rejected", "attention"}
    assert set(aml_router.MATCH_OUTCOMES.values()) <= allowed
    assert set(aml_router.CHECK_OUTCOMES.values()) <= allowed


def test_a_refusal_carries_complycubes_own_message() -> None:
    with_message = httpx.Response(400, json={"message": '"outcome" must be one of [confirmed]'})
    assert aml_router._refusal(with_message) == (
        'ComplyCube refused the request (400): "outcome" must be one of [confirmed]'
    )
    assert aml_router._refusal(httpx.Response(500, text="oops")) == (
        "ComplyCube refused the request (500)."
    )


# ---- the client page ----------------------------------------------------------------------------


async def test_the_client_page_shows_the_latest_check_of_each_kind(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    pdoc = bed.ref("pdoc")
    console["provider"].documents[pdoc] = {
        "id": pdoc,
        "images": [{"documentSide": "front", "contentType": "image/jpg"}],
    }
    old_identity = await _check(bed, bed.client_pk, "id1", kind="identity", status="rejected")
    old_aml = await _check(bed, bed.client_pk, "aml1", status="failed", decided=True)
    identity = await _check(bed, bed.client_pk, "id2", kind="identity")
    aml = await _check(bed, bed.client_pk, "aml2", matches=[None])
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        doc_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO compliance.documents (tenant_id, client_id, "
                        "  provider_document_id, document_type) "
                        "VALUES (:t, :c, :p, 'passport') RETURNING id"
                    ),
                    {"t": bed.tenant_id, "c": bed.client_pk, "p": pdoc},
                )
            ).scalar_one()
        )
        await s.execute(
            text(
                "UPDATE compliance.checks SET document_id = :d, provider_document_id = :p "
                "WHERE uid = :u"
            ),
            {"d": doc_id, "p": pdoc, "u": identity},
        )
    await _risk(
        bed,
        bed.client_pk,
        "medium",
        {
            "overall": "medium",
            "countryRisk": {"risk": "low", "country": "GB", "breakdown": []},
            "politicalExposureRisk": {"risk": "medium", "checkId": "x"},
            "occupationRisk": {
                "risk": "low",
                "occupationTitle": "Director",
                "occupationCategory": "Finance",
            },
            "updatedAt": "2026-09-30T10:00:00.000Z",
        },
    )
    page = await _client_page(console, bed.contact_uid)

    assert set(page) == {
        "key",
        "client",
        "awaiting_screen",
        "stale",
        "needs_attention",
        "reasons",
        "steps",
        "waiting_since",
        "age",
        "risk",
        "identity",
        "aml",
        "earlier",
        "notes",
        "screen_queued",
        "identity_upload",
    }
    assert page["notes"] == []
    assert page["screen_queued"] is False
    assert page["key"] == bed.contact_uid
    assert page["client"]["name"] == "Ada Lovelace"
    assert page["client"]["contact_uid"] == bed.contact_uid
    assert page["client"]["origin"] == "portal"
    assert page["client"]["tenant_uid"] == console["tenant_uid"]
    # The same shape GET /aml/checks/{uid} returns.
    assert set(page["identity"]) == {"check", "client", "matches", "identity", "history"}
    assert page["identity"]["check"]["uid"] == identity
    assert page["aml"]["check"]["uid"] == aml
    assert [m["decision"] for m in page["aml"]["matches"]] == [None]
    assert [(c["uid"], c["kind"], c["status"]) for c in page["earlier"]] == [
        (old_aml, "aml", "failed"),
        (old_identity, "identity", "rejected"),
    ]
    assert page["earlier"][0]["decided"] is True
    assert page["earlier"][0]["needs_attention"] is False
    assert page["awaiting_screen"] is False
    assert page["stale"] is False
    # An undecided identity refer and an open match.
    assert page["needs_attention"] is True
    assert page["risk"]["overall"] == "medium"
    assert page["risk"]["factors"] == [
        {"key": "country", "label": "Country", "risk": "low", "detail": "GB"},
        {
            "key": "political_exposure",
            "label": "Political exposure",
            "risk": "medium",
            "detail": None,
        },
        {"key": "occupation", "label": "Occupation", "risk": "low", "detail": "Director · Finance"},
        {"key": "watchlist", "label": "Watchlist", "risk": "not_set", "detail": None},
    ]
    assert page["risk"]["updated_at"] is not None
    # Showing the identity images records the view, against the identity check.
    assert page["identity"]["identity"]["images"] == [
        {"part": "front", "label": "ID front", "content_type": "image/jpeg", "source": "complycube"}
    ]
    views = await _views(bed, identity)
    assert [view["route"] for view in views] == ["/aml/clients/{key}"]
    assert [as_json(view["metadata"])["subject"] for view in views] == [{"image_count": 1}]


async def test_a_client_due_a_first_screen_opens_by_its_contact_uid(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    _c, bare_uid = await _bare_contact(bed, "due")
    page = await _client_page(console, bare_uid)
    assert page["key"] == bare_uid
    assert page["awaiting_screen"] is True
    assert page["needs_attention"] is True
    assert page["identity"] is None
    assert page["aml"] is None
    assert page["earlier"] == []
    assert page["risk"] is None
    assert page["client"]["name"] == "Client due"
    assert page["client"]["contact_uid"] == bare_uid
    assert page["client"]["origin"] == "portal"

    # The legacy `contact-<uid>` key still opens the client, and names the contact uid.
    legacy = await _client_page(console, f"{aml_router.CONTACT_KEY_PREFIX}{bare_uid}")
    assert legacy["key"] == bare_uid
    assert legacy["awaiting_screen"] is True
    # And once the client has a compliance client, the legacy key opens that client.
    screened = await _client_page(console, f"{aml_router.CONTACT_KEY_PREFIX}{bed.contact_uid}")
    assert screened["key"] == bed.contact_uid
    assert screened["client"]["name"] == "Ada Lovelace"


async def _queue_screen(bed: Bed, contact_id: int, *, status: str = "queued") -> None:
    """The flows outbox row the journey's AML step writes (``screen.enqueue``)."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "INSERT INTO flows.outbox (tenant_id, event_type, payload, status) "
                "VALUES (:t, 'aml.screen.run', CAST(:p AS jsonb), :st)"
            ),
            {"t": bed.tenant_id, "p": json.dumps({"contact_id": contact_id}), "st": status},
        )


async def test_a_screen_the_worker_has_queued_reads_as_starting(console: dict[str, Any]) -> None:
    """Identity passed and the AML step queued its screen: the console shows it starting, with no
    Screen button and nobody needing to act, until the worker runs it."""
    bed = console["bed"]
    contact_id, uid = await _bare_contact(bed, "queued")
    await _queue_screen(bed, contact_id)
    page = await _client_page(console, uid)
    assert page["awaiting_screen"] is True
    assert page["screen_queued"] is True
    assert page["needs_attention"] is False
    (row,) = (await _clients(console, contact=uid))["items"]
    assert row["aml"]["status"] == "awaiting_screen"
    assert row["aml"]["queued"] is True
    assert row["needs_attention"] is False


@pytest.mark.parametrize("outbox", [None, "delivered", "dead_letter"])
async def test_a_screen_that_did_not_start_still_needs_an_operator(
    console: dict[str, Any], outbox: str | None
) -> None:
    """Due a screen with none queued (never queued, or the job ended without a check): an operator
    screens the client, so the Screen button stays and the client needs attention."""
    bed = console["bed"]
    contact_id, uid = await _bare_contact(bed, f"stuck{outbox or 'none'}")
    if outbox is not None:
        await _queue_screen(bed, contact_id, status=outbox)
    page = await _client_page(console, uid)
    assert page["awaiting_screen"] is True
    assert page["screen_queued"] is False
    assert page["needs_attention"] is True
    (row,) = (await _clients(console, contact=uid))["items"]
    assert row["aml"]["queued"] is False
    assert row["needs_attention"] is True


@pytest.mark.parametrize("key", ["no-such-client", "contact-nobody-here"])
async def test_an_unknown_client_is_404(console: dict[str, Any], key: str) -> None:
    with pytest.raises(HTTPException) as raised:
        await _client_page(console, key)
    assert raised.value.status_code == 404


async def test_a_check_names_the_client_page_it_belongs_on(console: dict[str, Any]) -> None:
    """The console's one-check links (`/aml/{uid}`) redirect to the client page through this."""
    bed = console["bed"]
    uid = await _check(bed, bed.client_pk, "link", status="clear")
    async with _ctx(_ops(console)) as ctx:
        found = await aml_router.get_check_client(uid, ctx=ctx)
    assert found == {"key": bed.contact_uid, "check_uid": uid}
    with pytest.raises(HTTPException) as raised:
        async with _ctx(_ops(console)) as ctx:
            await aml_router.get_check_client("no-such-check", ctx=ctx)
    assert raised.value.status_code == 404


# ---- the client key -----------------------------------------------------------------------------


async def _partner_client(bed: Bed, tag: str) -> tuple[int, str]:
    """A partner-API client of the bed's firm: a compliance client with no contact, holding what
    the firm API sent about them.

    Returns ``(compliance client id, compliance client uid)``.
    """
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            await s.execute(
                text(
                    "INSERT INTO compliance.clients (tenant_id, provider_client_id, first_name, "
                    "  last_name, email, mobile, telephone, gender, joined_date) "
                    "VALUES (:t, :p, 'Grace', :last, :email, '07700900456', '01174960000', "
                    "  'female', '2024-03-01') RETURNING id, uid"
                ),
                {
                    "t": bed.tenant_id,
                    "p": f"cli_{tag}_{bed.nonce}",
                    "last": f"Hopper {tag}",
                    "email": f"grace-{tag}@example.test",
                },
            )
        ).one()
        await s.execute(
            text(
                "INSERT INTO compliance.addresses (tenant_id, client_id, building_name, "
                "  property_number, line, city, postal_code, country) "
                "VALUES (:t, :c, 'Hopper House', '7', 'Navy Row', 'Portsmouth', 'PO1 3LJ', 'GB')"
            ),
            {"t": bed.tenant_id, "c": int(row.id)},
        )
    return int(row.id), str(row.uid)


async def test_a_client_with_a_contact_is_keyed_by_the_contact_uid(
    console: dict[str, Any],
) -> None:
    """The list row, the hit row and the check's client link all carry the contact uid. A
    compliance uid still opens the client page, and the page names the contact uid to move to."""
    bed = console["bed"]
    _c, contact_uid, client_pk = await _contact(bed, "keyed")
    uid = await _check(bed, client_pk, "keyed", matches=[None])
    compliance_uid = await _client_key(bed, client_pk)
    assert compliance_uid != contact_uid

    assert (await _row(console, contact_uid))["key"] == contact_uid
    async with _ctx(_ops(console)) as ctx:
        assert (await aml_router.get_check_client(uid, ctx=ctx))["key"] == contact_uid

    by_contact = await _client_page(console, contact_uid)
    assert by_contact["key"] == contact_uid
    assert by_contact["aml"]["check"]["uid"] == uid
    by_uid = await _client_page(console, compliance_uid)
    assert by_uid["key"] == contact_uid
    assert by_uid["aml"]["check"]["uid"] == uid


async def test_a_partner_client_with_no_contact_keeps_its_compliance_uid(
    console: dict[str, Any],
) -> None:
    bed = console["bed"]
    client_pk, client_uid = await _partner_client(bed, "pkey")
    uid = await _check(bed, client_pk, "pkey", origin="partner", matches=[None])

    (row,) = (await _clients(console, q="hopper pkey"))["items"]
    assert row["key"] == client_uid
    assert row["contact_uid"] is None
    assert row["origin"] == "partner"
    assert row["client_name"] == "Grace Hopper pkey"
    async with _ctx(_ops(console)) as ctx:
        assert (await aml_router.get_check_client(uid, ctx=ctx))["key"] == client_uid
    page = await _client_page(console, client_uid)
    assert page["key"] == client_uid
    assert page["client"]["origin"] == "partner"
    assert page["aml"]["check"]["uid"] == uid


async def test_a_deleted_contact_still_opens_while_it_has_a_compliance_client(
    console: dict[str, Any],
) -> None:
    """The list keeps a screened client whose firm record was deleted, so its key must open."""
    bed = console["bed"]
    _c, gone_uid, gone_client = await _contact(bed, "gone")
    await _check(bed, gone_client, "gone", status="clear")
    _c, bare_uid = await _bare_contact(bed, "bare", awaiting=False)
    await _write(
        bed,
        "UPDATE crm.contacts SET deleted_at = now() WHERE uid = ANY(:u)",
        {"u": [gone_uid, bare_uid]},
    )
    assert (await _row(console, gone_uid))["key"] == gone_uid
    assert (await _client_page(console, gone_uid))["key"] == gone_uid
    # A deleted contact that was never screened is nobody's client here.
    with pytest.raises(HTTPException) as raised:
        await _client_page(console, bare_uid)
    assert raised.value.status_code == 404


async def test_a_client_key_is_held_to_the_narrowed_firm(console: dict[str, Any]) -> None:
    """Narrowed to another firm with `?tenant=`, no form of this firm's client key opens it."""
    bed = console["bed"]
    _c, bare_uid = await _bare_contact(bed, "scoped")
    partner_pk, partner_uid = await _partner_client(bed, "scoped")
    await _check(bed, partner_pk, "scoped", origin="partner")
    keys = {
        bed.contact_uid: bed.contact_uid,
        await _client_key(bed, bed.client_pk): bed.contact_uid,
        f"{aml_router.CONTACT_KEY_PREFIX}{bed.contact_uid}": bed.contact_uid,
        bare_uid: bare_uid,
        partner_uid: partner_uid,
    }
    other = await _other_tenant(console)
    for key, canonical in keys.items():
        with pytest.raises(HTTPException) as raised:
            async with _ctx(_ops(console), tenant=other) as ctx:
                await aml_router.get_client(key, ctx=ctx)
        assert raised.value.status_code == 404, key
        async with _ctx(_ops(console), tenant=console["tenant_uid"]) as ctx:
            assert (await aml_router.get_client(key, ctx=ctx))["key"] == canonical, key


# ---- the client's details -----------------------------------------------------------------------


async def _user(bed: Bed, tag: str, first: str, last: str) -> str:
    """A login in the bed's firm. Returns its ``auth.users`` id."""
    user_id = str(uuid.uuid4())
    await _write(
        bed,
        "INSERT INTO auth.users (id, tenant_id, email, first_name, last_name, workos_user_id) "
        "VALUES (:id, :t, :email, :first, :last, :w)",
        {
            "id": user_id,
            "t": bed.tenant_id,
            "email": f"{tag}-{bed.nonce}@aml.test",
            "first": first,
            "last": last,
            "w": f"wos_{tag}_{bed.nonce}",
        },
    )
    return user_id


async def test_a_portal_client_carries_the_firms_record_of_them(console: dict[str, Any]) -> None:
    """Every surface that shows a client shows the same facts, read from the contact."""
    bed = console["bed"]
    advisers: list[dict[str, Any]] = []
    # Only one `adviser` row per contact (0245): Lena holds it, Aaron is a support role --
    # `_user` never sets `is_adviser`, so both logins carry that nature flag as False.
    for tag, first, role in (("lead", "Lena", "adviser"), ("aaron", "Aaron", "support")):
        adviser = await _user(bed, tag, first, "Adviser")
        await _write(
            bed,
            "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
            "VALUES (:t, :c, :a, :r)",
            {"t": bed.tenant_id, "c": bed.contact_id, "a": adviser, "r": role},
        )
        (login,) = await fetch(
            bed.maker, "SELECT uid FROM auth.users WHERE id = CAST(:id AS uuid)", {"id": adviser}
        )
        advisers.append(
            {
                "uid": login["uid"],
                "name": f"{first} Adviser",
                "email": f"{tag}-{bed.nonce}@aml.test",
                "phone": None,
                "job_title": None,
                "fca_reference": None,
                "photo_url": None,
                "role": role,
                "is_adviser": False,
            }
        )
    await _write(
        bed,
        "UPDATE crm.contacts SET title = 'Countess', email = 'ada@example.test', "
        "  mobile = '07700900123', "
        "  phone = '01174960123', address = CAST(:address AS jsonb), "
        "  gender = 'female', employment_status = 'self_employed' WHERE id = :c",
        {
            "c": bed.contact_id,
            "address": json.dumps(
                {
                    "line1": "12 St James Square",
                    "line2": "Flat 2",
                    "city": "London",
                    "postcode": "SW1Y 4JH",
                    "country": "GB",
                }
            ),
        },
    )
    uid = await _check(bed, bed.client_pk, "facts", matches=[None])

    async with _ctx(_ops(console)) as ctx:
        client = (await aml_router.get_check(uid, ctx=ctx))["client"]
    assert client == {
        "name": "Ada Lovelace",
        "type": "individual",
        "title": "Countess",
        "dob": None,
        "nationality": "GB",
        "country": "GB",
        "email": "ada@example.test",
        "phone": "07700900123",
        "address": "12 St James Square, Flat 2, London, SW1Y 4JH, GB",
        "gender": "female",
        # No occupation held, so the employment status.
        "occupation": "Self employed",
        "client_since": await _created_day(bed, bed.contact_uid),
        # The adviser, though another sorts first by name.
        "adviser": "Lena Adviser",
        # Everyone on the contact's team, the adviser first.
        "advisers": advisers,
        "contact_uid": bed.contact_uid,
        "tenant_uid": console["tenant_uid"],
        "tenant_name": f"Firm ccf {bed.nonce}",
        "tenant_icon_url": None,
    }
    page = await _client_page(console, bed.contact_uid)
    assert page["client"] == {**client, "origin": "portal"}

    # An occupation wins over the employment status; with no mobile, the first other number.
    await _write(
        bed,
        "UPDATE crm.contacts SET occupation = 'Mathematician', mobile = NULL WHERE id = :c",
        {"c": bed.contact_id},
    )
    async with _ctx(_ops(console)) as ctx:
        client = (await aml_router.get_check(uid, ctx=ctx))["client"]
    assert client["occupation"] == "Mathematician"
    assert client["phone"] == "01174960123"


async def test_a_portal_client_is_named_as_the_client_record_names_them(
    console: dict[str, Any],
) -> None:
    """Journey kickoff writes ``name`` once and the fact-find never updates it; the fact-find
    fills title, first name and surname. Every console surface, and the task copy, reads the
    parts, as the client record does, so a stale kickoff ``name`` never shows."""
    bed = console["bed"]
    await _write(
        bed,
        "UPDATE crm.contacts SET name = 'Ada Augusta Byron', title = 'Countess', "
        "  first_name = 'Ada', surname = 'Lovelace' WHERE id = :c",
        {"c": bed.contact_id},
    )
    uid = await _check(bed, bed.client_pk, "named", matches=[None])
    named = "Countess Ada Lovelace"

    async with _ctx(_ops(console)) as ctx:
        assert (await aml_router.get_check(uid, ctx=ctx))["client"]["name"] == named
    assert (await _client_page(console, bed.contact_uid))["client"]["name"] == named
    assert (await _row(console, bed.contact_uid))["client_name"] == named
    (found,) = (await _clients(console, q="Lovelace"))["items"]
    assert found["key"] == bed.contact_uid
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert await tasks.client_label(s, bed.contact_id) == (named, f"Firm ccf {bed.nonce}")

    # With no first name or surname held, the record's own name; an organisation is its name.
    # The name is set in the same write: with both parts held it would follow them (0246).
    await _write(
        bed,
        "UPDATE crm.contacts SET first_name = NULL, surname = '', name = 'Ada Augusta Byron' "
        "WHERE id = :c",
        {"c": bed.contact_id},
    )
    assert (await _client_page(console, bed.contact_uid))["client"]["name"] == "Ada Augusta Byron"
    await _write(
        bed,
        "UPDATE crm.contacts SET type = 'organisation', name = 'Analytical Engines Ltd', "
        "  first_name = 'Ada', surname = 'Lovelace' WHERE id = :c",
        {"c": bed.contact_id},
    )
    assert (await _row(console, bed.contact_uid))["client_name"] == "Analytical Engines Ltd"


async def test_a_partner_client_carries_what_the_firm_api_sent(console: dict[str, Any]) -> None:
    bed = console["bed"]
    client_pk, client_uid = await _partner_client(bed, "pfacts")
    uid = await _check(bed, client_pk, "pfacts", origin="partner", matches=[None])

    async with _ctx(_ops(console)) as ctx:
        client = (await aml_router.get_check(uid, ctx=ctx))["client"]
    assert client == {
        "name": "Grace Hopper pfacts",
        "type": None,
        "title": None,
        "dob": None,
        "nationality": None,
        "country": "GB",
        "email": "grace-pfacts@example.test",
        "phone": "07700900456",
        "address": "Hopper House, 7 Navy Row, Portsmouth, PO1 3LJ, GB",
        "gender": "female",
        "occupation": None,
        # The day the firm says the client joined it.
        "client_since": "2024-03-01",
        "adviser": None,
        "advisers": [],
        "contact_uid": None,
        "tenant_uid": console["tenant_uid"],
        "tenant_name": f"Firm ccf {bed.nonce}",
        "tenant_icon_url": None,
    }
    page = await _client_page(console, client_uid)
    assert page["client"] == {**client, "origin": "partner"}

    # With no joined date, the day ComplyCube's client was first stored.
    await _write(
        bed,
        "UPDATE compliance.clients SET joined_date = NULL, mobile = NULL WHERE id = :c",
        {"c": client_pk},
    )
    created = await fetch(
        bed.maker, "SELECT created_at FROM compliance.clients WHERE id = :c", {"c": client_pk}
    )
    async with _ctx(_ops(console)) as ctx:
        client = (await aml_router.get_check(uid, ctx=ctx))["client"]
    assert client["client_since"] == created[0]["created_at"].date().isoformat()
    # With no mobile, the telephone.
    assert client["phone"] == "01174960000"


# ---- the firm's icon ----------------------------------------------------------------------------

#: A stored platform icon, in the same-origin proxy shape an upload returns.
ICON = "/_assets/firm-0123456789abcdef.png"


async def test_the_firms_platform_icon_rides_beside_its_name(console: dict[str, Any]) -> None:
    """The list row, the hit row, the check page and the client page all carry the icon the admin
    branding read serves for the firm's platform portal."""
    bed = console["bed"]
    theme = {
        "v": 2,
        "client": {"icon_url": "/_assets/client-0123456789abcdef.png"},
        "platform": {"icon_url": ICON},
    }
    await _write(
        bed,
        "UPDATE auth.tenants SET theme = CAST(:theme AS jsonb) WHERE id = :t",
        {"theme": json.dumps(theme), "t": bed.tenant_id},
    )
    uid = await _check(bed, bed.client_pk, "icon", matches=[None])
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        served = (await branding.get_branding(s, bed.tenant_id, "platform"))["branding"]
    assert isinstance(served, dict)
    assert served["icon_url"] == ICON

    assert (await _row(console, bed.contact_uid))["tenant_icon_url"] == ICON
    async with _ctx(_ops(console)) as ctx:
        assert (await aml_router.get_check(uid, ctx=ctx))["client"]["tenant_icon_url"] == ICON
    assert (await _client_page(console, bed.contact_uid))["client"]["tenant_icon_url"] == ICON

    # A firm with no platform icon gets None, whatever its client portal carries.
    await _write(
        bed,
        "UPDATE auth.tenants SET theme = CAST(:theme AS jsonb) WHERE id = :t",
        {"theme": json.dumps({"v": 2, "client": {"icon_url": ICON}}), "t": bed.tenant_id},
    )
    assert (await _row(console, bed.contact_uid))["tenant_icon_url"] is None


def test_a_platform_icon_is_read_as_the_branding_read_serves_it() -> None:
    icon = aml_router._platform_icon
    assert icon(None) is None
    assert icon({}) is None
    assert icon({"v": 2, "platform": {"icon_url": ICON}}) == ICON
    assert icon(json.dumps({"v": 2, "platform": {"icon_url": ICON}})) == ICON
    assert icon({"v": 2, "client": {"icon_url": ICON}}) is None
    # A pre-per-portal theme is one branding for both portals.
    assert icon({"icon_url": ICON}) == ICON
    # An old public-bucket URL is served as the same-origin proxy path.
    bucket = "https://bucket.example/files/tenant/firm-0123456789abcdef.png"
    assert icon({"v": 2, "platform": {"icon_url": bucket}}) == ICON
    # An inline icon too large to repeat on every row of a page is withheld.
    huge = "data:image/png;base64," + "A" * aml_router._ICON_MAX_CHARS
    assert icon({"v": 2, "platform": {"icon_url": huge}}) is None
    small = "data:image/png;base64,AAAA"
    assert icon({"v": 2, "platform": {"icon_url": small}}) == small
    # A theme that does not validate is served as no branding, never an error.
    assert icon({"v": 2, "platform": {"icon_url": "javascript:alert(1)"}}) is None
    assert icon("not json") is None


async def test_a_match_carries_what_its_raw_item_says(console: dict[str, Any]) -> None:
    bed = console["bed"]
    uid = await _check(bed, bed.client_pk, "rawm")
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        check_pk = (
            await s.execute(text("SELECT id FROM compliance.checks WHERE uid = :u"), {"u": uid})
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO compliance.matches "
                "  (tenant_id, check_id, provider_match_id, score, raw) "
                "VALUES (:t, :c, 'mraw', 93, CAST(:raw AS jsonb))"
            ),
            {"t": bed.tenant_id, "c": check_pk, "raw": json.dumps(DOCUMENTED_MATCH)},
        )
    async with _ctx(_ops(console)) as ctx:
        detail = await aml_router.get_check(uid, ctx=ctx)
    (match,) = detail["matches"]
    assert match["name"] == "Viktor Vladimirovich Sheyman"
    assert match["score"] == 93.0
    assert match["gender"] == "male"
    assert match["dob"] == "1958-05-26"
    assert match["countries"] == ["BY"]
    assert match["categories"] == ["PEP level 2", "PEP level 1"]
    assert match["addresses"] == [
        {"line": "Karl Marx, 38", "city": "Minsk", "postal_code": "220016", "country": "BY"}
    ]
    assert match["references"][0] == {
        "source": "EC 2015/1949 Belarus List",
        "date": "2015-10-29",
        "to_date": None,
        "url": None,
    }
    assert match["references"][1]["url"] == "https://sanctions.example/belarus"


async def test_a_match_carries_the_profile_of_who_it_matched(console: dict[str, Any]) -> None:
    """The check page and the client page each carry the match's profile."""
    bed = console["bed"]
    uid = await _check(bed, bed.client_pk, "pep")
    await _write(
        bed,
        "INSERT INTO compliance.matches (tenant_id, check_id, provider_match_id, score, raw) "
        "SELECT :t, id, 'mpep', 98, CAST(:raw AS jsonb) FROM compliance.checks WHERE uid = :u",
        {"t": bed.tenant_id, "u": uid, "raw": json.dumps(PEP_MATCH)},
    )
    async with _ctx(_ops(console)) as ctx:
        detail = await aml_router.get_check(uid, ctx=ctx)
    (match,) = detail["matches"]
    assert match["name"] == "Nicola Ferguson Sturgeon"
    assert {key: match[key] for key in PEP_PROFILE} == PEP_PROFILE
    page = await _client_page(console, bed.contact_uid)
    assert page["aml"]["matches"] == [match]


#: One match as ComplyCube's AML Screening Check reference documents it (trimmed), plus a
#: reference URL, which that reference does not show but the console passes on when sent.
DOCUMENTED_MATCH: dict[str, Any] = {
    "id": "5ebe5880fd7de4000876118f",
    "score": 100,
    "status": "attention",
    "details": {
        "entityName": "Viktor Vladimirovich Sheyman",
        "entityType": "person",
        "gender": "male",
        "deceased": False,
        "countries": [
            {"type": "Born in", "country": "BY"},
            {"type": "Citizenship", "country": "BY"},
        ],
        "dates": [{"type": "Date of Birth", "date": {"day": "26", "month": "05", "year": "1958"}}],
        "addresses": [
            {"line": "Karl Marx, 38", "city": "Minsk", "postalCode": "220016", "country": "BY"}
        ],
        "references": [
            {
                "source": "EC 2015/1949 Belarus List",
                "fromDate": {"day": "29", "month": "10", "year": "2015"},
            },
            {
                "source": "EC 2016/276 Belarus List",
                "fromDate": {"day": "25", "month": "02", "year": "2016"},
                "url": "https://sanctions.example/belarus",
            },
        ],
        "classifications": {"pep": ["level2", "level1"]},
    },
    "breakdown": [{"fieldName": "name", "score": 93}],
}


def test_match_facts_tolerate_any_shape() -> None:
    empty = {
        "name": None,
        "gender": None,
        "dob": None,
        "countries": [],
        "addresses": [],
        "references": [],
        "categories": [],
        "score": None,
    }
    for raw in (None, "", "not json", [], {"details": "nope"}, {"details": {"dates": "x"}}):
        assert aml_router.match_facts(raw) == empty, raw
    junk = aml_router.match_facts(
        {
            "details": {
                "dates": [{"type": "Date of Birth", "data": {"year": "1958"}}, "junk"],
                "countries": [{"type": "x"}, {"country": "GB"}, {"country": "GB"}, 7],
                "addresses": [{}, {"city": "Leeds"}],
                "references": [
                    {"source": "A list", "url": "javascript:alert(1)"},
                    {},
                    {"name": "B list", "fromDate": "2020"},
                ],
                "classifications": {
                    "watchlist": "sanctionsLists",
                    "adverseMedia": ["regulatory", 3],
                    "otherLists": [],
                    "": ["x"],
                },
            }
        },
        ["pep", "adverse_media"],
    )
    assert junk["dob"] == "1958"
    assert junk["countries"] == ["GB"]
    assert junk["addresses"] == [
        {"line": None, "city": "Leeds", "postal_code": None, "country": None}
    ]
    assert junk["references"] == [
        {"source": "A list", "date": None, "to_date": None, "url": None},
        {"source": "B list", "date": "2020", "to_date": None, "url": None},
    ]
    assert junk["categories"] == [
        "Sanctions lists",
        "Adverse media: regulatory",
        "Other lists",
        "PEP",
        "Adverse media",
    ]
    # Fields at the top level, for an item that was never wrapped in `details`.
    top_level = aml_router.match_facts(json.dumps({"entityName": "Top Level", "score": 81.5}))
    assert (top_level["name"], top_level["score"]) == ("Top Level", 81.5)
    assert aml_router.match_facts({"score": True})["score"] is None


def _date(year: str, month: str | None = None, day: str | None = None) -> dict[str, str]:
    """ComplyCube's structured date."""
    parts = {"year": year, "month": month, "day": day}
    return {key: value for key, value in parts.items() if value is not None}


#: A live ComplyCube PEP match (May 2026), trimmed, with a junk image, a duplicate and a non-http
#: source link, and an associate with no name added.
PEP_MATCH: dict[str, Any] = {
    "id": "6650c0ffee",
    "score": 98,
    "status": "attention",
    "details": {
        "entityName": "Nicola Ferguson Sturgeon",
        "entityType": "person",
        "gender": "female",
        "active": True,
        "deceased": False,
        "lastReviewedDate": "14-May-2026",
        "types": ["Politically Exposed Person (PEP)", "Special Interest Person (SIP)"],
        "images": [
            "https://img.example/sturgeon-1.jpg",
            "https://img.example/sturgeon-2.jpg",
            "https://img.example/sturgeon-3.jpg",
            "javascript:x",
        ],
        "names": [
            {
                "type": "primary",
                "entityName": "Nicola Ferguson Sturgeon",
                "firstName": "Nicola",
                "middleName": "Ferguson",
                "lastName": "Sturgeon",
            },
            {"type": "alias", "entityName": "Nicola Sturgeon"},
            {"type": "localised_name", "entityName": "Никола Стерджен"},
            {"type": "alias", "firstName": "Nikki", "lastName": "Sturgeon"},
        ],
        "birthPlaces": ["Irvine,United Kingdom"],
        "countries": [
            {"type": "Born in", "country": "GB"},
            {"type": "Citizenship", "country": "GB"},
        ],
        "dates": [
            {"type": "Date of Birth", "date": _date("1970", "07", "19")},
            {"type": "Inactive as of (PEP)", "date": _date("2026", "05", "14")},
        ],
        "occupations": [
            {"type": "Primary Occupation", "title": "See Previous Roles", "category": "Other"},
            {
                "type": "Previous Roles",
                "title": "First Minister of Scotland",
                "category": "Heads & Deputy Heads of Regional Government",
                "fromDate": _date("2014", "11", "19"),
                "toDate": _date("2023", "03", "27"),
            },
            {
                "type": "Previous Roles",
                "title": "Member, Scottish Parliament",
                "fromDate": _date("1999", "05", "06"),
                "toDate": _date("2026", "05", "14"),
            },
            {"type": "Primary Occupation", "title": "Author", "fromDate": _date("2024")},
        ],
        "associations": [
            {
                "entityName": "Alexander Elliot Anderson Salmond",
                "entityType": "person",
                "relationship": "Friend",
                "current": False,
                "images": ["https://ichef.bbci.co.uk/a.jpg"],
                "classifications": [
                    {"type": "pep", "subType": "level1"},
                    {"type": "pep", "subType": "level3"},
                ],
            },
            {"relationship": "Spouse"},
        ],
        "notes": "Long narrative about a career in Scottish politics…",
        "sourceLinks": ["https://www.bbc.com/news/x", "https://www.bbc.com/news/x", "ftp://nope"],
    },
}

#: What ``match_profile`` reads from ``PEP_MATCH``.
PEP_PROFILE: dict[str, Any] = {
    # The javascript: image is not a link.
    "images": [
        "https://img.example/sturgeon-1.jpg",
        "https://img.example/sturgeon-2.jpg",
        "https://img.example/sturgeon-3.jpg",
    ],
    "names": [
        {"name": "Nicola Ferguson Sturgeon", "type": "Primary"},
        {"name": "Nicola Sturgeon", "type": "Alias"},
        {"name": "Никола Стерджен", "type": "Localised name"},
        # A name given in parts, with no entityName.
        {"name": "Nikki Sturgeon", "type": "Alias"},
    ],
    # Every name but the primary one.
    "aliases": ["Nicola Sturgeon", "Никола Стерджен", "Nikki Sturgeon"],
    # "See Previous Roles" is a pointer, not a role. The current role first, then the latest start.
    "roles": [
        {
            "title": "Author",
            "category": None,
            "type": "Primary Occupation",
            "from": "2024",
            "to": None,
            "current": True,
        },
        {
            "title": "First Minister of Scotland",
            "category": "Heads & Deputy Heads of Regional Government",
            "type": "Previous Roles",
            "from": "2014-11-19",
            "to": "2023-03-27",
            "current": False,
        },
        {
            "title": "Member, Scottish Parliament",
            "category": None,
            "type": "Previous Roles",
            "from": "1999-05-06",
            "to": "2026-05-14",
            "current": False,
        },
    ],
    "birth_places": ["Irvine, United Kingdom"],
    "country_details": [
        {"country": "GB", "type": "Born in"},
        {"country": "GB", "type": "Citizenship"},
    ],
    "dates": [
        {"type": "Date of Birth", "date": "1970-07-19"},
        {"type": "Inactive as of (PEP)", "date": "2026-05-14"},
    ],
    "entity_type": "person",
    "notes": "Long narrative about a career in Scottish politics…",
    # The associate with no name is left out.
    "associations": [
        {
            "name": "Alexander Elliot Anderson Salmond",
            "relationship": "Friend",
            "current": False,
            "categories": ["PEP level 1", "PEP level 3"],
            "entity_type": "person",
            "image": "https://ichef.bbci.co.uk/a.jpg",
        }
    ],
    # Once each, and http(s) only.
    "source_links": ["https://www.bbc.com/news/x"],
    "types": ["Politically Exposed Person (PEP)", "Special Interest Person (SIP)"],
    "active": True,
    "deceased": False,
    "last_reviewed": "14-May-2026",
}

#: ``match_profile`` of anything it cannot read.
EMPTY_PROFILE: dict[str, Any] = {
    "images": [],
    "names": [],
    "aliases": [],
    "roles": [],
    "birth_places": [],
    "country_details": [],
    "dates": [],
    "entity_type": None,
    "notes": None,
    "associations": [],
    "source_links": [],
    "types": [],
    "active": None,
    "deceased": None,
    "last_reviewed": None,
}


def test_a_match_profile_reads_a_real_pep_match() -> None:
    assert aml_router.match_profile(PEP_MATCH) == PEP_PROFILE
    # The same item as stored text.
    assert aml_router.match_profile(json.dumps(PEP_MATCH)) == PEP_PROFILE


def test_a_match_profile_tolerates_any_shape() -> None:
    for raw in (
        None,
        "",
        "not json",
        [],
        {"details": "nope"},
        {"details": {"names": "x", "occupations": [1], "associations": [{}]}},
        {
            "details": {
                "active": "yes",
                "deceased": 0,
                "types": "PEP",
                "birthPlaces": "Irvine",
                "images": "https://img.example/a.jpg",
                "sourceLinks": [7, None],
                "dates": [{"type": "Date of Birth"}, "junk"],
                "countries": [{"type": "Born in"}],
                "occupations": [{"type": "Previous Roles"}, {"title": "  "}],
            }
        },
    ):
        assert aml_router.match_profile(raw) == EMPTY_PROFILE, raw


def test_a_match_profile_caps_every_list() -> None:
    limits = aml_router._PROFILE_LIMITS
    many = 80
    profile = aml_router.match_profile(
        {
            "details": {
                "images": [f"https://img.example/{i}.jpg" for i in range(many)],
                "names": [{"type": "alias", "entityName": f"Name {i}"} for i in range(many)],
                "occupations": [
                    {"type": "Previous Roles", "title": f"Role {i}"} for i in range(many)
                ],
                "associations": [{"entityName": f"Associate {i}"} for i in range(many)],
                "sourceLinks": [f"https://news.example/{i}" for i in range(many)],
                "notes": "x" * (aml_router._NOTES_LIMIT * 2),
            }
        }
    )
    assert len(profile["images"]) == limits["images"] == 10
    assert len(profile["names"]) == limits["names"]
    assert len(profile["aliases"]) == limits["names"]
    assert len(profile["roles"]) == limits["roles"]
    assert len(profile["associations"]) == limits["associations"]
    assert len(profile["source_links"]) == limits["links"]
    assert profile["notes"] == "x" * aml_router._NOTES_LIMIT
    # The first of each are the ones kept.
    assert profile["images"][0] == "https://img.example/0.jpg"
    assert profile["associations"][0]["name"] == "Associate 0"


def test_a_structured_date_keeps_only_the_parts_it_has() -> None:
    assert aml_router.structured_date({"day": "3", "month": "4", "year": "1990"}) == "1990-04-03"
    assert aml_router.structured_date({"month": "04", "year": 1990}) == "1990-04"
    assert aml_router.structured_date({"day": "3", "year": "1990"}) == "1990"
    assert aml_router.structured_date({"day": "3"}) is None
    assert aml_router.structured_date(" 1990 ") == "1990"
    assert aml_router.structured_date(None) is None


def test_a_risk_profile_reads_each_part() -> None:
    profile = aml_router.risk_profile("not_set", {"overallRisk": "very_high"}, None)
    assert profile["overall"] == "very_high"
    assert {factor["key"]: factor["risk"] for factor in profile["factors"]} == {
        "country": "not_set",
        "political_exposure": "not_set",
        "occupation": "not_set",
        "watchlist": "not_set",
    }
    assert profile["updated_at"] is None
    assert aml_router.risk_profile("low", "{}", None)["overall"] == "low"
    assert aml_router.risk_profile(None, None, None)["overall"] == "not_set"
    dated = aml_router.risk_profile("high", {"updatedAt": "2026-01-01T00:00:00Z"}, None)
    assert dated["updated_at"] == "2026-01-01T00:00:00Z"


#: A real ComplyCube identity check result (2 Oct 2026), with a specimen match list added in the
#: shape ComplyCube's console shows one.
REAL_IDENTITY_RESULT = {
    "outcome": "attention",
    "breakdown": {
        "faceAnalysis": {
            "breakdown": {"documentSideUsed": "front", "facialSimilarityScore": 0},
            "facialSimilarity": "attention",
            "bannedFacesAnalysis": "not_processed",
            "previouslyEnrolledFace": "clear",
        },
        "integrityAnalysis": {"vpnDetected": "not_processed", "faceDetection": "clear"},
        "authenticityAnalysis": {
            "breakdown": {
                "livenessCheckScore": 83,
                "specimenMatches": ["https://example.test/a.jpg", "https://example.test/b.jpg"],
            },
            "livenessCheck": "clear",
            "spoofedImageAnalysis": "attention",
        },
        "extractedData": {"firstName": "Not echoed"},
    },
}


def test_the_identity_analysis_reads_like_complycubes_breakdown() -> None:
    analysis = aml_router.identity_analysis(REAL_IDENTITY_RESULT)
    groups = {
        g["label"]: {i["label"]: i["outcome"] for i in g["items"]} for g in analysis["groups"]
    }
    assert groups == {
        "Face analysis": {
            "Facial similarity": "attention",
            "Banned faces analysis": "not_processed",
            "Previously enrolled face": "clear",
        },
        "Integrity analysis": {"Vpn detected": "not_processed", "Face detection": "clear"},
        "Authenticity analysis": {
            "Liveness check": "clear",
            "Spoofed image analysis": "attention",
        },
    }
    assert {s["label"]: s["value"] for s in analysis["scores"]} == {
        "Facial similarity score": 0,
        "Liveness check score": 83,
    }
    assert analysis["facts"] == [
        {"key": "faceAnalysis.documentSideUsed", "label": "Document side used", "value": "front"}
    ]
    assert analysis["links"] == [
        {
            "key": "authenticityAnalysis.specimenMatches",
            "label": "Specimen matches",
            "urls": ["https://example.test/a.jpg", "https://example.test/b.jpg"],
        }
    ]
    assert "Not echoed" not in str(analysis)


def test_the_identity_analysis_tolerates_a_missing_or_odd_result() -> None:
    empty = {"groups": [], "scores": [], "facts": [], "links": []}
    assert aml_router.identity_analysis(None) == empty
    assert aml_router.identity_analysis({"breakdown": ["junk"]}) == empty
    loose = aml_router.identity_analysis({"breakdown": {"documentCheck": "clear"}})
    assert loose["groups"] == [
        {
            "key": "other",
            "label": "Other",
            "items": [{"key": "documentCheck", "label": "Document check", "outcome": "clear"}],
        }
    ]
