"""Router-function-level tests for the "Link to IO" card: `crm.search_io_clients` (the search
step) and `crm.link_contact_to_io` (recording the pick on `platform.links`).

The search step now DOES touch a real Postgres: `search_io_clients` builds its Intelliflo filter
from the contact's own stored name/NINO (`_io_clients_for_book_contact`), never from caller input
-- a caller-supplied filter used to let any `contacts:write` user list or match any client in the
single, process-wide Intelliflo tenant this deployment holds credentials for, not just their own
book's. `link_contact_to_io` re-runs that same search to verify a `client_id_io` is actually one of
this contact's own candidates before storing anything.

Tests monkeypatch `intelliflo.client.get_clients`/`get_client_plans` rather than hitting a real
Intelliflo sandbox. Only `tenant_id` needs to be a real row (the `auth.tenants` FK on
`platform.links`); `entity_id` carries no FK, so an arbitrary contact/account id is fine where the
test doesn't need the search/verification path to resolve a real one."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from intelliflo import client as intelliflo_client
from intelliflo.client import IntellifloApiError, IntellifloAuthError
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.db import FirmContext
from app.routers import crm as crm_router

WRITE = ["contacts:write"]

pytestmark = pytest.mark.usefixtures("clear_io_plan_links")


def _principal(perms: list[str]) -> Principal:
    return Principal(user_id="user_io", organisation_id="org_io", permissions=perms)


@pytest.fixture
def io_tenant_id(super_engine: Engine) -> int:
    """One real `auth.tenants` row -- nothing else about it matters to these tests."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        return int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"iolinkten_{s}", "a": f"io-link-{s}", "n": f"IO Link {s}"},
            ).scalar_one()
        )


@pytest.fixture
def io_contact_id(super_engine: Engine, io_tenant_id: int) -> int:
    """A bare `crm.contacts` row under `io_tenant_id` -- just enough for `get_contact` to 200."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        return int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts "
                    "  (tenant_id, uid, type, first_name, surname, service_type) "
                    "VALUES (:t, :uid, 'individual', 'Io', 'Linker', 'advised') RETURNING id"
                ),
                {"t": io_tenant_id, "uid": f"iolinkcon_{s}"},
            ).scalar_one()
        )


@pytest.fixture
def io_account_id(super_engine: Engine, io_tenant_id: int, io_contact_id: int) -> int:
    """A bare `platform.accounts` row under `io_contact_id` -- just enough for `get_account`."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        return int(
            c.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, contact_id, uid, status, value) "
                    "VALUES (:t, :con, :uid, CAST('active' AS platform.account_status), 0) "
                    "RETURNING id"
                ),
                {"t": io_tenant_id, "con": io_contact_id, "uid": f"iolinkacc_{s}"},
            ).scalar_one()
        )


@pytest.mark.asyncio
async def test_search_io_clients_searches_by_this_contacts_own_identity(
    service_session: AsyncSession, io_contact_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No caller-supplied filter reaches Intelliflo at all -- the search is built entirely from
    this book contact's own stored name (`io_contact_id`'s fixture: first_name='Io',
    surname='Linker', no NINO on file)."""
    seen: dict[str, Any] = {}

    async def _stub(**kwargs: Any) -> list[dict[str, Any]]:
        seen.update(kwargs)
        return [{"id": "io-1", "name": "John Smith", "externalReference": "EXT-001"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)

    result = await crm_router.search_io_clients(
        contact_id=io_contact_id, principal=_principal(WRITE), session=service_session
    )

    assert result == [
        crm_router.IoClientOut(
            client_id_io="io-1",
            display_name="John Smith - EXT-001",
            raw={"id": "io-1", "name": "John Smith", "externalReference": "EXT-001"},
        )
    ]
    assert seen == {"first_name": "Io", "last_name": "Linker"}


def test_search_tiers_never_fall_back_to_a_single_name() -> None:
    """The Intelliflo tenant is shared by every firm, so a first-name-only or surname-only search
    would return other firms' clients. Only the NINO and the full name are ever tried."""
    person = {
        "type": "individual",
        "first_name": "John",
        "surname": "Smith",
        "national_identifier": "QQ123456C",
        "national_identifier_type": "nino",
    }
    assert crm_router._io_client_search_tiers(person) == [
        {"ni_number": "QQ123456C"},
        {"first_name": "John", "last_name": "Smith"},
    ]
    for partial in ({"surname": None}, {"first_name": None}):
        no_nino = {**person, "national_identifier": None, "national_identifier_type": None}
        assert crm_router._io_client_search_tiers({**no_nino, **partial}) == []


@pytest.mark.asyncio
async def test_search_io_clients_never_calls_intelliflo_with_no_usable_identity(
    service_session: AsyncSession, super_engine: Engine, io_tenant_id: int
) -> None:
    """A contact with no name or NINO on file at all builds no search tiers -- the whole point of
    `_io_client_search_tiers` rejecting an empty filter is that `get_clients()` with every field
    blank is Intelliflo's entire directory, not "no match"."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        blank_contact_id = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, service_type) "
                    "VALUES (:t, :uid, 'individual', 'advised') RETURNING id"
                ),
                {"t": io_tenant_id, "uid": f"blankcon_{s}"},
            ).scalar_one()
        )
        c.commit()

    # No monkeypatch on `get_clients` at all: an unconfigured real client would raise/time out on
    # any actual network call, so a clean `[]` back is itself proof nothing was attempted.
    result = await crm_router._io_clients_for_book_contact(service_session, blank_contact_id)
    assert result == []


@pytest.mark.asyncio
async def test_search_io_clients_requires_contacts_write(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(**_: Any) -> list[dict[str, Any]]:
        raise AssertionError("Intelliflo must not be called when the permission gate 403s first")

    monkeypatch.setattr(intelliflo_client, "get_clients", _boom)

    with pytest.raises(HTTPException) as exc:
        await crm_router.search_io_clients(contact_id=1, principal=_principal([]))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_cls", [IntellifloAuthError, IntellifloApiError])
async def test_search_io_clients_translates_intelliflo_errors_to_502(
    service_session: AsyncSession,
    io_contact_id: int,
    monkeypatch: pytest.MonkeyPatch,
    exc_cls: type[Exception],
) -> None:
    async def _boom(**_: Any) -> list[dict[str, Any]]:
        raise exc_cls("upstream is down")

    monkeypatch.setattr(intelliflo_client, "get_clients", _boom)

    with pytest.raises(HTTPException) as exc:
        await crm_router.search_io_clients(
            contact_id=io_contact_id, principal=_principal(WRITE), session=service_session
        )

    assert exc.value.status_code == 502


# ============================================================================
# link_contact_to_io
# ============================================================================


def _stub_get_clients(monkeypatch: pytest.MonkeyPatch, *clients: dict[str, Any]) -> None:
    """Make `intelliflo_client.get_clients` return exactly these candidates, whatever tier it was
    called with -- the tests below are about verification, not the tiering itself (that is
    `test_search_io_clients_searches_by_this_contacts_own_identity`'s own job)."""

    async def _stub(**_: Any) -> list[dict[str, Any]]:
        return list(clients)

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)


@pytest.mark.asyncio
async def test_link_contact_to_io_inserts_and_returns_the_mapped_shape(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_get_clients(
        monkeypatch, {"id": "io-42", "name": "Jane Doe", "externalReference": "EXT-042"}
    )
    body = crm_router.LinkIoClientBody(client_id_io="io-42")
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))

    result = await crm_router.link_contact_to_io(contact_id=io_contact_id, body=body, ctx=ctx)

    expected_raw = {"id": "io-42", "name": "Jane Doe", "externalReference": "EXT-042"}
    assert result == crm_router.IoClientOut(
        client_id_io="io-42", display_name="Jane Doe - EXT-042", raw=expected_raw
    )

    row = (
        (
            await service_session.execute(
                text(
                    "SELECT external_id, data FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'contact' AND entity_id = :cid "
                    "  AND provider = 'intelliflo' AND link_type = 'primary'"
                ),
                {"t": io_tenant_id, "cid": io_contact_id},
            )
        )
        .mappings()
        .one()
    )
    assert row["external_id"] == "io-42"
    assert row["data"] == expected_raw


@pytest.mark.asyncio
async def test_link_contact_to_io_upserts_a_second_pick(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Picking a different match later REPLACES the link -- there is only one slot
    (`tenant_id, provider, entity_type, entity_id, link_type` is unique), by design."""
    _stub_get_clients(
        monkeypatch, {"id": "io-1", "name": "First"}, {"id": "io-2", "name": "Second"}
    )
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))

    await crm_router.link_contact_to_io(
        contact_id=io_contact_id,
        body=crm_router.LinkIoClientBody(client_id_io="io-1"),
        ctx=ctx,
    )
    await crm_router.link_contact_to_io(
        contact_id=io_contact_id,
        body=crm_router.LinkIoClientBody(client_id_io="io-2"),
        ctx=ctx,
    )

    rows = (
        (
            await service_session.execute(
                text(
                    "SELECT external_id FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'contact' AND entity_id = :cid "
                    "  AND provider = 'intelliflo' AND link_type = 'primary'"
                ),
                {"t": io_tenant_id, "cid": io_contact_id},
            )
        )
        .mappings()
        .all()
    )
    assert [dict(r) for r in rows] == [{"external_id": "io-2"}]


@pytest.mark.asyncio
async def test_link_contact_to_io_rejects_a_client_id_not_among_this_contacts_own_candidates(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core fix: a `client_id_io` that this contact's own search never surfaced must not be
    storable, however it was obtained (another contact's search, a guess) -- the single Intelliflo
    credential behind this deployment is process-wide, not scoped per portals tenant."""
    _stub_get_clients(monkeypatch, {"id": "io-999", "name": "Someone Else"})
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))

    with pytest.raises(HTTPException) as exc:
        await crm_router.link_contact_to_io(
            contact_id=io_contact_id,
            body=crm_router.LinkIoClientBody(client_id_io="io-1"),
            ctx=ctx,
        )

    assert exc.value.status_code == 400
    row = (
        await service_session.execute(
            text(
                "SELECT 1 FROM platform.links "
                "WHERE tenant_id = :t AND entity_type = 'contact' AND entity_id = :cid "
                "  AND provider = 'intelliflo' AND link_type = 'primary'"
            ),
            {"t": io_tenant_id, "cid": io_contact_id},
        )
    ).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_link_contact_to_io_requires_contacts_write(
    service_session: AsyncSession, io_tenant_id: int
) -> None:
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal([]))

    with pytest.raises(HTTPException) as exc:
        await crm_router.link_contact_to_io(
            contact_id=900_003,
            body=crm_router.LinkIoClientBody(client_id_io="io-1"),
            ctx=ctx,
        )

    assert exc.value.status_code == 403


# ============================================================================
# get_contact's io_link read-back
# ============================================================================


@pytest.mark.asyncio
async def test_get_contact_carries_the_link_back(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The card has to show a persisted link on a plain page reload, not just right after the
    POST in the same browser session -- `get_contact` is where every other section (kyc,
    term_acceptances) already rides along on one load instead of a second fetch."""
    _stub_get_clients(
        monkeypatch, {"id": "io-99", "name": "Io Linker", "externalReference": "EXT-099"}
    )
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    await crm_router.link_contact_to_io(
        contact_id=io_contact_id,
        body=crm_router.LinkIoClientBody(client_id_io="io-99"),
        ctx=ctx,
    )

    detail = await crm_router.get_contact(contact_id=io_contact_id, session=service_session)

    assert detail["io_link"] == {
        "client_id_io": "io-99",
        "display_name": "Io Linker - EXT-099",
    }


@pytest.mark.asyncio
async def test_get_contact_io_link_is_none_when_unlinked(
    service_session: AsyncSession, io_contact_id: int
) -> None:
    detail = await crm_router.get_contact(contact_id=io_contact_id, session=service_session)

    assert detail["io_link"] is None


# ============================================================================
# _io_plan_out / planType is a NESTED OBJECT, not a flat string
# ============================================================================


def test_io_plan_out_reads_name_and_portfolio_category_off_the_nested_plan_type() -> None:
    """A live Intelliflo plan's `planType` is `{id, name, portfolioCategory, ...}` -- an earlier
    version read `raw.get("planType")`/`raw.get("portfolioCategory")` as flat top-level fields,
    which rendered the dropdown's label as the nested dict's `repr()` instead of its name."""
    raw = {
        "id": "p1",
        "reference": "ACC-001",
        "planType": {"id": 18, "name": "AVC", "portfolioCategory": "Pensions", "region": "UK"},
    }

    out = crm_router._io_plan_out(raw)

    assert out.plan_type == "AVC"
    assert out.portfolio_category == "Pensions"


def test_io_plan_out_tolerates_a_missing_plan_type() -> None:
    out = crm_router._io_plan_out({"id": "p1", "reference": "ACC-001"})

    assert out.plan_type is None
    assert out.portfolio_category is None


# ============================================================================
# the account record's "IO Plans" card (_io_plans_for_contact / get_account)
# ============================================================================


@pytest.mark.asyncio
async def test_io_plans_empty_when_the_client_was_never_linked(
    service_session: AsyncSession, io_contact_id: int
) -> None:
    assert await crm_router._io_plans_for_contact(service_session, io_contact_id) == []


@pytest.mark.asyncio
async def test_io_plans_fetches_by_the_linked_clients_io_id(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    _stub_get_clients(monkeypatch, {"id": "io-77"})
    await crm_router.link_contact_to_io(
        contact_id=io_contact_id,
        body=crm_router.LinkIoClientBody(client_id_io="io-77"),
        ctx=ctx,
    )

    seen: dict[str, Any] = {}

    async def _stub(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        seen["client_id_io"] = client_id_io
        return [
            {"id": "p1", "policyNumber": "POL-1", "reference": "ACC-001"},
            {"id": "p2", "policyNumber": "POL-2", "reference": "ACC-002"},
        ]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _stub)

    plans = await crm_router._io_plans_for_contact(service_session, io_contact_id)

    assert seen == {"client_id_io": "io-77"}
    assert plans == [
        crm_router.IoPlanOut(
            plan_id_io="p1",
            policy_number="POL-1",
            reference="ACC-001",
            raw={"id": "p1", "policyNumber": "POL-1", "reference": "ACC-001"},
        ),
        crm_router.IoPlanOut(
            plan_id_io="p2",
            policy_number="POL-2",
            reference="ACC-002",
            raw={"id": "p2", "policyNumber": "POL-2", "reference": "ACC-002"},
        ),
    ]


@pytest.mark.asyncio
async def test_io_plans_filters_out_plans_with_no_policy_number(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plan's `external_id` (and so its place in the dropdown) is keyed on `policyNumber` --
    one that is absent, blank or whitespace-only cannot be linked to, so it has no business
    appearing as a choice."""
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    _stub_get_clients(monkeypatch, {"id": "io-77"})
    await crm_router.link_contact_to_io(
        contact_id=io_contact_id,
        body=crm_router.LinkIoClientBody(client_id_io="io-77"),
        ctx=ctx,
    )

    async def _stub(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [
            {"id": "p1", "policyNumber": "POL-1"},
            {"id": "p2"},
            {"id": "p3", "policyNumber": ""},
            {"id": "p4", "policyNumber": "   "},
        ]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _stub)

    plans = await crm_router._io_plans_for_contact(service_session, io_contact_id)

    assert [p.plan_id_io for p in plans] == ["p1"]


@pytest.mark.asyncio
async def test_io_plans_degrades_to_empty_on_a_vendor_failure(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No search step to retry from here (unlike the client page), so a vendor outage must not
    fail the whole account page -- it degrades to an empty dropdown instead."""
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    _stub_get_clients(monkeypatch, {"id": "io-77"})
    await crm_router.link_contact_to_io(
        contact_id=io_contact_id,
        body=crm_router.LinkIoClientBody(client_id_io="io-77"),
        ctx=ctx,
    )

    async def _boom(*_: Any, **__: Any) -> list[dict[str, Any]]:
        raise IntellifloApiError("upstream is down")

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _boom)

    assert await crm_router._io_plans_for_contact(service_session, io_contact_id) == []


@pytest.mark.asyncio
async def test_get_account_carries_io_plans(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    _stub_get_clients(monkeypatch, {"id": "io-77"})
    await crm_router.link_contact_to_io(
        contact_id=io_contact_id,
        body=crm_router.LinkIoClientBody(client_id_io="io-77"),
        ctx=ctx,
    )

    async def _stub(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [{"id": "p1", "policyNumber": "POL-1", "reference": "ACC-001"}]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _stub)

    detail = await crm_router.get_account(account_id=io_account_id, session=service_session)

    assert detail["io_plans"] == [
        {
            "plan_id_io": "p1",
            "policy_number": "POL-1",
            "reference": "ACC-001",
            "plan_type": None,
            "portfolio_category": None,
            "raw": {"id": "p1", "policyNumber": "POL-1", "reference": "ACC-001"},
        }
    ]
    assert detail["io_link"] is None


async def _link_client_with_plans(
    session: AsyncSession,
    ctx: FirmContext,
    contact_id: int,
    monkeypatch: pytest.MonkeyPatch,
    plans: list[dict[str, Any]],
) -> None:
    """Link `contact_id` to an Intelliflo client that owns `plans`. An account can only be linked
    to one of its client's own plans, so a test of the account link starts here."""
    _stub_get_clients(monkeypatch, {"id": "io-77"})
    await crm_router.link_contact_to_io(
        contact_id=contact_id, body=crm_router.LinkIoClientBody(client_id_io="io-77"), ctx=ctx
    )

    async def _plans_stub(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return plans

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans_stub)


@pytest.mark.asyncio
async def test_link_account_to_io_inserts_and_returns_the_mapped_shape(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    plan = {"id": "p1", "reference": "ACC-001"}
    await _link_client_with_plans(service_session, ctx, io_contact_id, monkeypatch, [plan])
    body = crm_router.LinkIoPlanBody(plan_id_io="p1", raw={"id": "p1", "reference": "IGNORED"})

    result = await crm_router.link_account_to_io(account_id=io_account_id, body=body, ctx=ctx)

    assert result == crm_router.IoPlanOut(plan_id_io="p1", reference="ACC-001", raw=plan)

    row = (
        (
            await service_session.execute(
                text(
                    "SELECT external_id, data FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'account' AND entity_id = :aid "
                    "  AND provider = 'intelliflo' AND link_type = 'primary'"
                ),
                {"t": io_tenant_id, "aid": io_account_id},
            )
        )
        .mappings()
        .one()
    )
    assert row["external_id"] == "p1"
    assert row["data"] == plan


@pytest.mark.asyncio
async def test_link_account_to_io_upserts_a_second_pick(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    await _link_client_with_plans(
        service_session, ctx, io_contact_id, monkeypatch, [{"id": "p1"}, {"id": "p2"}]
    )

    await crm_router.link_account_to_io(
        account_id=io_account_id,
        body=crm_router.LinkIoPlanBody(plan_id_io="p1", raw={"id": "p1"}),
        ctx=ctx,
    )
    await crm_router.link_account_to_io(
        account_id=io_account_id,
        body=crm_router.LinkIoPlanBody(plan_id_io="p2", raw={"id": "p2"}),
        ctx=ctx,
    )

    rows = (
        (
            await service_session.execute(
                text(
                    "SELECT external_id FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'account' AND entity_id = :aid "
                    "  AND provider = 'intelliflo' AND link_type = 'primary'"
                ),
                {"t": io_tenant_id, "aid": io_account_id},
            )
        )
        .mappings()
        .all()
    )
    assert [dict(r) for r in rows] == [{"external_id": "p2"}]


@pytest.mark.asyncio
async def test_link_account_to_io_requires_contacts_write(
    service_session: AsyncSession, io_tenant_id: int, io_account_id: int
) -> None:
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal([]))

    with pytest.raises(HTTPException) as exc:
        await crm_router.link_account_to_io(
            account_id=io_account_id,
            body=crm_router.LinkIoPlanBody(plan_id_io="p1", raw={"id": "p1"}),
            ctx=ctx,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_account_carries_the_plan_link_back(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    await _link_client_with_plans(
        service_session, ctx, io_contact_id, monkeypatch, [{"id": "p1", "reference": "ACC-001"}]
    )
    await crm_router.link_account_to_io(
        account_id=io_account_id,
        body=crm_router.LinkIoPlanBody(plan_id_io="p1", raw={"id": "p1"}),
        ctx=ctx,
    )

    detail = await crm_router.get_account(account_id=io_account_id, session=service_session)

    assert detail["io_link"] == {
        "plan_id_io": "p1",
        "policy_number": None,
        "reference": "ACC-001",
        "plan_type": None,
        "portfolio_category": None,
    }


@pytest.mark.asyncio
async def test_link_account_to_io_stores_the_policy_number_as_external_id(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`external_id` is the thing an adviser recognises against this account -- the policy
    number -- not the vendor's bare plan id, even though `plan_id_io` is what the frontend binds
    the dropdown's selection to."""
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    plan = {"id": "p1", "policyNumber": "POL-1", "reference": "ACC-001"}
    await _link_client_with_plans(service_session, ctx, io_contact_id, monkeypatch, [plan])
    body = crm_router.LinkIoPlanBody(plan_id_io="p1", raw=plan)

    result = await crm_router.link_account_to_io(account_id=io_account_id, body=body, ctx=ctx)

    assert result == crm_router.IoPlanOut(
        plan_id_io="p1", policy_number="POL-1", reference="ACC-001", raw=plan
    )

    row = (
        (
            await service_session.execute(
                text(
                    "SELECT external_id FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'account' AND entity_id = :aid "
                    "  AND provider = 'intelliflo' AND link_type = 'primary'"
                ),
                {"t": io_tenant_id, "aid": io_account_id},
            )
        )
        .mappings()
        .one()
    )
    assert row["external_id"] == "POL-1"


@pytest.mark.asyncio
async def test_link_account_to_io_skips_the_patch_with_no_custody_link(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bare `io_account_id` fixture carries no Seccl/SEI/... link -- nothing to push into
    Intelliflo as a policy number, so the PATCH must not even be attempted."""

    async def _boom(*_: Any, **__: Any) -> None:
        raise AssertionError("must not call Intelliflo with no custody link to source a number")

    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    await _link_client_with_plans(
        service_session, ctx, io_contact_id, monkeypatch, [{"id": "p1", "policyNumber": "POL-1"}]
    )
    monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _boom)

    result = await crm_router.link_account_to_io(
        account_id=io_account_id,
        body=crm_router.LinkIoPlanBody(plan_id_io="p1", raw={"id": "p1"}),
        ctx=ctx,
    )

    assert result.policy_number == "POL-1"


@pytest.mark.asyncio
async def test_link_account_to_io_refuses_a_plan_when_the_client_has_no_io_link(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no Intelliflo client linked there is nothing to check the plan against, so a plan id
    the caller merely names is refused (409) and nothing is stored. The unique index on the plan id
    is global across tenants: storing an unverified claim would reserve another firm's plan."""

    async def _boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("must not call Intelliflo with no client to ask about")

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _boom)
    monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _boom)
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))

    with pytest.raises(HTTPException) as refused:
        await crm_router.link_account_to_io(
            account_id=io_account_id,
            body=crm_router.LinkIoPlanBody(plan_id_io="p1", raw={"id": "forged"}),
            ctx=ctx,
        )

    assert refused.value.status_code == 409
    stored = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM platform.links WHERE tenant_id = :t "
                "AND entity_type = 'account' AND entity_id = :aid AND provider = 'intelliflo'"
            ),
            {"t": io_tenant_id, "aid": io_account_id},
        )
    ).scalar_one()
    assert stored == 0


@pytest.mark.asyncio
async def test_link_account_to_io_verifies_the_plan_with_no_custody_link(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An IO client link is enough to verify: a plan that client does not own is refused even
    when the account has no custody number to push, and an owned plan is stored from
    Intelliflo's payload, not the caller's, with no PATCH."""
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    _stub_get_clients(monkeypatch, {"id": "io-77"})
    await crm_router.link_contact_to_io(
        contact_id=io_contact_id, body=crm_router.LinkIoClientBody(client_id_io="io-77"), ctx=ctx
    )

    async def _plans_stub(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [{"id": "p1", "policyNumber": "IO-POL", "reference": "ACC-001"}]

    async def _boom(*_: Any, **__: Any) -> None:
        raise AssertionError("no custody number, so nothing to push")

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans_stub)
    monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _boom)

    with pytest.raises(HTTPException) as refused:
        await crm_router.link_account_to_io(
            account_id=io_account_id,
            body=crm_router.LinkIoPlanBody(plan_id_io="someone-elses", raw={"id": "someone-elses"}),
            ctx=ctx,
        )
    assert refused.value.status_code == 400

    result = await crm_router.link_account_to_io(
        account_id=io_account_id,
        body=crm_router.LinkIoPlanBody(
            plan_id_io="p1", raw={"id": "p1", "reference": "SHOULD-NOT-LAND"}
        ),
        ctx=ctx,
    )
    assert (result.policy_number, result.reference) == ("IO-POL", "ACC-001")


async def _seed_account_plan_link(
    session: AsyncSession, tenant_id: int, account_id: int, plan_id: str
) -> None:
    """An account already linked to `plan_id`, written directly: the state the cross-tenant tests
    start from, which the endpoint itself can no longer produce without a verified client."""
    await session.execute(
        text(
            "INSERT INTO platform.links "
            "  (tenant_id, entity_type, entity_id, provider, link_type, external_id, data) "
            "VALUES (:t, 'account', :aid, 'intelliflo', 'primary', :plan, CAST(:data AS jsonb))"
        ),
        {"t": tenant_id, "aid": account_id, "plan": plan_id, "data": json.dumps({"id": plan_id})},
    )


@pytest.mark.asyncio
async def test_a_plan_linked_in_another_tenant_cannot_be_linked_again(
    service_session: AsyncSession,
    super_engine: Engine,
    io_tenant_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every portals tenant reaches the same Intelliflo tenant, so the one-plan-per-account rule
    has to hold across tenants: a second firm linking the plan the first already has is refused
    (409), whatever its own tenant's look-up could see."""
    await _seed_account_plan_link(service_session, io_tenant_id, io_account_id, "shared-plan")
    tenant, _, account = _second_firm_account(super_engine, plan_owner_link=True)

    async def _plans(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [{"id": "shared-plan"}]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans)
    second = FirmContext(session=service_session, tenant_id=tenant, principal=_principal(WRITE))
    with pytest.raises(HTTPException) as refused:
        await crm_router.link_account_to_io(
            account_id=account,
            body=crm_router.LinkIoPlanBody(plan_id_io="shared-plan", raw={"id": "shared-plan"}),
            ctx=second,
        )
    assert refused.value.status_code == 409


def _second_firm_account(super_engine: Engine, *, plan_owner_link: bool) -> tuple[int, int, int]:
    """A second tenant with a contact, an account and (optionally) the contact's own Intelliflo
    client link and a custody number: everything `link_account_to_io` needs to push a number."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"iosec_{s}", "a": f"io-sec-{s}", "n": f"IO Second {s}"},
            ).scalar_one()
        )
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts "
                    "  (tenant_id, uid, type, first_name, surname, service_type) "
                    "VALUES (:t, :uid, 'individual', 'Io', 'Second', 'advised') RETURNING id"
                ),
                {"t": tenant, "uid": f"ioseccon_{s}"},
            ).scalar_one()
        )
        account = int(
            c.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, contact_id, uid, status, value) "
                    "VALUES (:t, :con, :uid, CAST('active' AS platform.account_status), 0) "
                    "RETURNING id"
                ),
                {"t": tenant, "con": contact, "uid": f"iosecacc_{s}"},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                "VALUES (:t, 'account', :aid, 'seccl', 'primary', 'SECCL-SECOND')"
            ),
            {"t": tenant, "aid": account},
        )
        if plan_owner_link:
            c.execute(
                text(
                    "INSERT INTO platform.links "
                    "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                    "VALUES (:t, 'contact', :cid, 'intelliflo', 'primary', 'io-second')"
                ),
                {"t": tenant, "cid": contact},
            )
    return tenant, contact, account


@pytest.mark.asyncio
async def test_a_plan_linked_in_another_tenant_is_never_written_to_in_intelliflo(
    service_session: AsyncSession,
    super_engine: Engine,
    io_tenant_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The unique index only answers when the row is written, so the row must be written BEFORE this
    account's number is pushed into the plan: pushing first overwrote the other firm's plan in
    Intelliflo and then failed the local write, leaving nothing to undo it."""
    await _seed_account_plan_link(service_session, io_tenant_id, io_account_id, "shared-plan")
    tenant, _, account = _second_firm_account(super_engine, plan_owner_link=True)

    async def _plans(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [{"id": "shared-plan"}]

    pushes: list[tuple[str, str, str]] = []

    async def _push(client_id_io: str, plan_id_io: str, policy_number: str, **_: Any) -> None:
        pushes.append((client_id_io, plan_id_io, policy_number))

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans)
    monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _push)
    second = FirmContext(session=service_session, tenant_id=tenant, principal=_principal(WRITE))

    with pytest.raises(HTTPException) as refused:
        await crm_router.link_account_to_io(
            account_id=account,
            body=crm_router.LinkIoPlanBody(plan_id_io="shared-plan", raw={"id": "shared-plan"}),
            ctx=second,
        )

    assert refused.value.status_code == 409
    assert pushes == []


@pytest.mark.asyncio
async def test_a_failed_push_leaves_no_local_link_behind(
    service_session: AsyncSession,
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, _, account = _second_firm_account(super_engine, plan_owner_link=True)

    async def _plans(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [{"id": "p9"}]

    async def _push(*_: Any, **__: Any) -> None:
        raise IntellifloApiError("PATCH plan failed: 500")

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans)
    monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _push)
    ctx = FirmContext(session=service_session, tenant_id=tenant, principal=_principal(WRITE))

    with pytest.raises(HTTPException) as failed:
        await crm_router.link_account_to_io(
            account_id=account,
            body=crm_router.LinkIoPlanBody(plan_id_io="p9", raw={"id": "p9"}),
            ctx=ctx,
        )

    assert failed.value.status_code == 502
    left = (
        await service_session.execute(
            text(
                "SELECT count(*) FROM platform.links WHERE tenant_id = :t "
                "AND entity_type = 'account' AND entity_id = :aid AND provider = 'intelliflo'"
            ),
            {"t": tenant, "aid": account},
        )
    ).scalar_one()
    assert left == 0


@pytest.mark.asyncio
async def test_link_account_to_io_refuses_with_a_custody_link_but_no_io_client_link(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A custody link alone isn't enough -- the plan is checked against, and pushed under, the
    contact's own IO client, and this fixture's contact was never linked to one: refused (409),
    nothing pushed."""
    with_custody = (
        "INSERT INTO platform.links "
        "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
        "VALUES (:t, 'account', :aid, 'seccl', 'primary', :ext)"
    )
    await service_session.execute(
        text(with_custody), {"t": io_tenant_id, "aid": io_account_id, "ext": "SECCL-ACC-1"}
    )

    async def _boom(*_: Any, **__: Any) -> None:
        raise AssertionError("must not call Intelliflo with no IO client link for the contact")

    monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _boom)
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))

    with pytest.raises(HTTPException) as refused:
        await crm_router.link_account_to_io(
            account_id=io_account_id,
            body=crm_router.LinkIoPlanBody(plan_id_io="p1", raw={"id": "p1"}),
            ctx=ctx,
        )

    assert refused.value.status_code == 409


@pytest.mark.asyncio
async def test_link_account_to_io_pushes_the_custody_account_number_into_intelliflo(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a custody link present (Seccl, here) AND the contact already linked to an IO client,
    linking a plan must PATCH that account number into Intelliflo (scoped under the contact's own
    IO client id) as the plan's `policyNumber`, and store the SAME number as our own link's
    `external_id` -- both sides end up agreeing on one number."""
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    _stub_get_clients(monkeypatch, {"id": "io-77"})
    await crm_router.link_contact_to_io(
        contact_id=io_contact_id,
        body=crm_router.LinkIoClientBody(client_id_io="io-77"),
        ctx=ctx,
    )
    with_custody = (
        "INSERT INTO platform.links "
        "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
        "VALUES (:t, 'account', :aid, 'seccl', 'primary', :ext)"
    )
    await service_session.execute(
        text(with_custody), {"t": io_tenant_id, "aid": io_account_id, "ext": "SECCL-ACC-1"}
    )

    async def _plans_stub(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [{"id": "p1", "policyNumber": "OLD-POL-1", "reference": "ACC-001"}]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans_stub)

    seen: dict[str, Any] = {}

    async def _stub(client_id_io: str, plan_id_io: str, policy_number: str, **_: Any) -> None:
        seen["client_id_io"] = client_id_io
        seen["plan_id_io"] = plan_id_io
        seen["policy_number"] = policy_number

    monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _stub)

    result = await crm_router.link_account_to_io(
        account_id=io_account_id,
        # `raw` is a caller claim that the push path now ignores entirely: the row actually
        # stored comes from `get_client_plans`'s own verified payload (`_plans_stub`, above),
        # not this.
        body=crm_router.LinkIoPlanBody(
            plan_id_io="p1",
            raw={"id": "p1", "policyNumber": "POL-1", "reference": "SHOULD-NOT-LAND"},
        ),
        ctx=ctx,
    )

    assert seen == {"client_id_io": "io-77", "plan_id_io": "p1", "policy_number": "SECCL-ACC-1"}
    assert result.policy_number == "SECCL-ACC-1"
    assert result.reference == "ACC-001"

    row = (
        (
            await service_session.execute(
                text(
                    "SELECT external_id, data FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'account' AND entity_id = :aid "
                    "  AND provider = 'intelliflo' AND link_type = 'primary'"
                ),
                {"t": io_tenant_id, "aid": io_account_id},
            )
        )
        .mappings()
        .one()
    )
    assert row["external_id"] == "SECCL-ACC-1"
    assert row["data"]["policyNumber"] == "SECCL-ACC-1"


@pytest.mark.asyncio
async def test_link_account_to_io_rejects_a_plan_not_belonging_to_this_client(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The matching account-side fix: with a custody link AND an IO client link present, a
    `plan_id_io` that client's own `get_client_plans` does not list must not have this account's
    custody number PATCHed onto it, and must not be stored either."""
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    _stub_get_clients(monkeypatch, {"id": "io-77"})
    await crm_router.link_contact_to_io(
        contact_id=io_contact_id,
        body=crm_router.LinkIoClientBody(client_id_io="io-77"),
        ctx=ctx,
    )
    with_custody = (
        "INSERT INTO platform.links "
        "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
        "VALUES (:t, 'account', :aid, 'seccl', 'primary', :ext)"
    )
    await service_session.execute(
        text(with_custody), {"t": io_tenant_id, "aid": io_account_id, "ext": "SECCL-ACC-1"}
    )

    async def _plans_stub(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [{"id": "p-some-other-plan"}]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans_stub)

    async def _boom(*_: Any, **__: Any) -> None:
        raise AssertionError("must not PATCH a plan that was never verified")

    monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _boom)

    with pytest.raises(HTTPException) as exc:
        await crm_router.link_account_to_io(
            account_id=io_account_id,
            body=crm_router.LinkIoPlanBody(plan_id_io="p1", raw={"id": "p1"}),
            ctx=ctx,
        )

    assert exc.value.status_code == 400
    row = (
        await service_session.execute(
            text(
                "SELECT 1 FROM platform.links "
                "WHERE tenant_id = :t AND entity_type = 'account' AND entity_id = :aid "
                "  AND provider = 'intelliflo' AND link_type = 'primary'"
            ),
            {"t": io_tenant_id, "aid": io_account_id},
        )
    ).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_link_account_to_io_translates_an_intelliflo_patch_failure_to_502(
    service_session: AsyncSession,
    io_tenant_id: int,
    io_contact_id: int,
    io_account_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = FirmContext(session=service_session, tenant_id=io_tenant_id, principal=_principal(WRITE))
    _stub_get_clients(monkeypatch, {"id": "io-77"})
    await crm_router.link_contact_to_io(
        contact_id=io_contact_id,
        body=crm_router.LinkIoClientBody(client_id_io="io-77"),
        ctx=ctx,
    )
    with_custody = (
        "INSERT INTO platform.links "
        "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
        "VALUES (:t, 'account', :aid, 'seccl', 'primary', :ext)"
    )
    await service_session.execute(
        text(with_custody), {"t": io_tenant_id, "aid": io_account_id, "ext": "SECCL-ACC-1"}
    )

    async def _plans_stub(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [{"id": "p1"}]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans_stub)

    async def _boom(*_: Any, **__: Any) -> None:
        raise IntellifloApiError("upstream is down")

    monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _boom)

    with pytest.raises(HTTPException) as exc:
        await crm_router.link_account_to_io(
            account_id=io_account_id,
            body=crm_router.LinkIoPlanBody(plan_id_io="p1", raw={"id": "p1"}),
            ctx=ctx,
        )

    assert exc.value.status_code == 502
