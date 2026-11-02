"""Router-function-level tests for the admin "Clients and Accounts" tab
(`integrations.py`'s `clients_table`/`io_clients_for_contact`/`accounts_for_contact`/
`io_accounts_for_client`/`link_client_and_account_to_io`) -- the admin-ops twin of the platform
portal's "Link to IO" cards, reusing the exact same `crm._upsert_contact_io_link`/
`_upsert_account_io_link` helpers so the two paths cannot drift.

`clients_table` is scoped to one tenant -- the integration's own `firm_id` -- rather than taking a
`?tenant=` directly; every other endpoint here derives its tenant from the picked contact/account
row instead. `admin_session` is mirrored by `service_session` the same way `test_integrations.py`
already does for every other admin_session-gated function.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from intelliflo import client as intelliflo_client
from intelliflo.client import IntellifloApiError, IntellifloAuthError
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.routers import integrations as router

READ = ["integrations:read"]
WRITE = ["integrations:write"]

pytestmark = pytest.mark.usefixtures("clear_io_plan_links")


def _principal(perms: list[str]) -> Principal:
    return Principal(user_id="wos_ops", organisation_id=None, role="ops", permissions=perms)


async def _all_clients_table_rows(
    session: AsyncSession, integration_uid: str
) -> list[router.ClientsTableRowOut]:
    """Every row across every page -- `clients_table` clamps `page_size` server-side
    (`_CLIENTS_TABLE_MAX_PAGE_SIZE`), so a single large `page_size` request silently gets capped.
    The full test suite accumulates thousands of `crm.contacts` rows across other files'
    (untruncated) fixtures, so a freshly-inserted contact is not guaranteed to land on page 1."""
    items: list[router.ClientsTableRowOut] = []
    page = 1
    while True:
        result = await router.clients_table(
            integration_uid=integration_uid,
            page=page,
            page_size=100,
            principal=_principal(READ),
            session=session,
        )
        items.extend(result.items)
        if len(items) >= result.total or not result.items:
            return items
        page += 1


def _insert_tenant(super_engine: Engine) -> int:
    """A second tenant, for the cases about one tenant's rows leaking into another's."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        return int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"catwo_{s}", "a": f"ca-two-{s}", "n": f"CA Other Tenant {s}"},
            ).scalar_one()
        )


@pytest.fixture
def ca_tenant_id(super_engine: Engine) -> int:
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        return int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"caten_{s}", "a": f"ca-{s}", "n": f"CA Tenant {s}"},
            ).scalar_one()
        )


@pytest.fixture
def ca_integration_uid(super_engine: Engine, ca_tenant_id: int) -> str:
    """One `integrations.platforms` row whose `firm_id` is `ca_tenant_id` -- `clients_table`'s own
    scope for this test file's whole suite."""
    s = uuid.uuid4().hex[:8]
    uid = f"caint_{s}"
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO integrations.platforms "
                "  (uid, firm_id, platform_name, tenant_id, tenant_name, provider_id) "
                "VALUES (:uid, :firm_id, 'SEI Wealth Platform', :tid, 'CA Tenant', '5261')"
            ),
            {"uid": uid, "firm_id": ca_tenant_id, "tid": f"tenant-{uid}"},
        )
        c.commit()
    return uid


def _insert_contact(
    super_engine: Engine,
    tenant_id: int,
    *,
    first_name: str = "Jane",
    surname: str = "Doe",
    national_identifier: str | None = None,
    national_identifier_type: str | None = None,
) -> tuple[int, str]:
    s = uuid.uuid4().hex[:8]
    uid = f"cacon_{s}"
    with super_engine.connect() as c:
        contact_id = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts "
                    "  (tenant_id, uid, type, first_name, surname, service_type, "
                    "   national_identifier, national_identifier_type) "
                    "VALUES (:t, :uid, 'individual', :fn, :sn, 'advised', :ni, :nit) "
                    "RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "uid": uid,
                    "fn": first_name,
                    "sn": surname,
                    "ni": national_identifier,
                    "nit": national_identifier_type,
                },
            ).scalar_one()
        )
        c.commit()
    return contact_id, uid


def _insert_account(
    super_engine: Engine,
    tenant_id: int,
    contact_id: int,
    *,
    name: str | None = None,
    account_number: str | None = None,
    custodian: str | None = None,
) -> tuple[int, str]:
    """An account. Its account number, when given, is the `external_id` of its SEI link -- where a
    synced account has one. It is never written to a column on the account row."""
    s = uuid.uuid4().hex[:8]
    uid = f"caacc_{s}"
    with super_engine.connect() as c:
        account_id = int(
            c.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, contact_id, uid, status, value, name, custodian) "
                    "VALUES (:t, :con, :uid, CAST('active' AS platform.account_status), 0, "
                    "        :name, CAST(:custodian AS platform.provider_code)) "
                    "RETURNING id"
                ),
                {
                    "t": tenant_id,
                    "con": contact_id,
                    "uid": uid,
                    "name": name,
                    "custodian": custodian,
                },
            ).scalar_one()
        )
        c.commit()
    if account_number is not None:
        _insert_account_link(super_engine, tenant_id, account_id, external_id=account_number)
    return account_id, uid


def _insert_account_link(
    super_engine: Engine,
    tenant_id: int,
    account_id: int,
    *,
    provider: str = "sei",
    external_id: str,
) -> None:
    """A primary `platform.links` row on an account -- where a synced custodian account's number
    lives."""
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                "VALUES (:t, 'account', :aid, CAST(:p AS platform.provider_code), 'primary', :ext)"
            ),
            {"t": tenant_id, "aid": account_id, "p": provider, "ext": external_id},
        )
        c.commit()


async def _search(
    session: AsyncSession, integration_uid: str, term: str | None, **kw: Any
) -> router.ClientsTablePageOut:
    return await router.clients_table(
        integration_uid=integration_uid,
        page=kw.get("page", 1),
        page_size=kw.get("page_size", 10),
        search=term,
        principal=_principal(READ),
        session=session,
    )


# ============================================================================
# clients_table
# ============================================================================


@pytest.mark.asyncio
async def test_clients_table_includes_a_client_of_the_integrations_own_firm(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    contact_id, contact_uid = _insert_contact(
        super_engine, ca_tenant_id, first_name="Zoltan", surname="Zebra"
    )
    _insert_account(super_engine, ca_tenant_id, contact_id)

    rows = await _all_clients_table_rows(service_session, ca_integration_uid)

    assert any(r.contact_uid == contact_uid for r in rows)


@pytest.mark.asyncio
async def test_clients_table_excludes_a_client_of_a_different_tenant(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_integration_uid: str,
) -> None:
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        other_tenant_id = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"othten_{s}", "a": f"other-{s}", "n": f"Other Tenant {s}"},
            ).scalar_one()
        )
        c.commit()
    contact_id, contact_uid = _insert_contact(super_engine, other_tenant_id)
    _insert_account(super_engine, other_tenant_id, contact_id)

    rows = await _all_clients_table_rows(service_session, ca_integration_uid)

    assert all(r.contact_uid != contact_uid for r in rows)


@pytest.mark.asyncio
async def test_clients_table_returns_nothing_when_the_integration_has_no_firm_assigned(
    service_session: AsyncSession, super_engine: Engine, ca_tenant_id: int
) -> None:
    s = uuid.uuid4().hex[:8]
    uid = f"caint_{s}"
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO integrations.platforms "
                "  (uid, firm_id, platform_name, tenant_id, tenant_name) "
                "VALUES (:uid, NULL, 'SEI Wealth Platform', :tid, 'No Firm Yet')"
            ),
            {"uid": uid, "tid": f"tenant-{uid}"},
        )
        c.commit()
    contact_id, _ = _insert_contact(super_engine, ca_tenant_id)
    _insert_account(super_engine, ca_tenant_id, contact_id)

    result = await router.clients_table(
        integration_uid=uid,
        page=1,
        page_size=10,
        principal=_principal(READ),
        session=service_session,
    )

    assert result.items == []
    assert result.total == 0


@pytest.mark.asyncio
async def test_clients_table_404s_for_an_unknown_integration_uid(
    service_session: AsyncSession,
) -> None:
    with pytest.raises(HTTPException) as exc:
        await router.clients_table(
            integration_uid="does-not-exist",
            page=1,
            page_size=10,
            principal=_principal(READ),
            session=service_session,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_clients_table_paginates(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    for i in range(3):
        contact_id, _ = _insert_contact(
            super_engine, ca_tenant_id, first_name="Page", surname=f"Test{i}"
        )
        _insert_account(super_engine, ca_tenant_id, contact_id)

    page1 = await router.clients_table(
        integration_uid=ca_integration_uid,
        page=1,
        page_size=2,
        principal=_principal(READ),
        session=service_session,
    )
    page2 = await router.clients_table(
        integration_uid=ca_integration_uid,
        page=2,
        page_size=2,
        principal=_principal(READ),
        session=service_session,
    )

    assert len(page1.items) == 2
    assert page1.total == page2.total
    assert {r.contact_uid for r in page1.items}.isdisjoint({r.contact_uid for r in page2.items})


# ----------------------------------------------------------------------------
# clients_table search
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clients_table_search_matches_the_client_name(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    token = uuid.uuid4().hex[:8]
    hit_id, hit_uid = _insert_contact(
        super_engine, ca_tenant_id, first_name="Halloway", surname=f"Quill{token}"
    )
    _insert_account(super_engine, ca_tenant_id, hit_id)
    miss_id, miss_uid = _insert_contact(
        super_engine, ca_tenant_id, first_name="Other", surname="Person"
    )
    _insert_account(super_engine, ca_tenant_id, miss_id)

    surname = await _search(
        service_session, ca_integration_uid, f"quill{token}"
    )  # case-insensitive
    assert [r.contact_uid for r in surname.items] == [hit_uid]
    assert surname.total == 1
    # The full name, first then last -- what an operator types when they have the whole name.
    full = await _search(service_session, ca_integration_uid, f"Halloway Quill{token}")
    assert [r.contact_uid for r in full.items] == [hit_uid]
    assert miss_uid not in {r.contact_uid for r in surname.items + full.items}


@pytest.mark.asyncio
async def test_clients_table_search_matches_an_account_name_and_still_lists_the_client(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    token = uuid.uuid4().hex[:8]
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    _insert_account(super_engine, ca_tenant_id, contact_id, name=f"Offshore Bond {token}")

    result = await _search(service_session, ca_integration_uid, f"offshore bond {token}")

    assert [r.contact_uid for r in result.items] == [contact_uid]


@pytest.mark.asyncio
async def test_clients_table_search_matches_the_account_number_wherever_it_lives(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    """A number is the `external_id` of a link on the account, or the account `uid` the dropdown
    falls back to. A `platform_identifier` on the account row is NOT searched: it is not where the
    number lives, and a second place to look is a second source of truth."""
    token = uuid.uuid4().hex[:8]
    by_link, by_link_uid = _insert_contact(super_engine, ca_tenant_id)
    _insert_account(super_engine, ca_tenant_id, by_link, account_number=f"MSP{token}")
    by_uid, by_uid_uid = _insert_contact(super_engine, ca_tenant_id)
    _, account_uid = _insert_account(super_engine, ca_tenant_id, by_uid)
    by_column, _ = _insert_contact(super_engine, ca_tenant_id)
    column_account, _ = _insert_account(super_engine, ca_tenant_id, by_column)
    with super_engine.connect() as c:
        c.execute(
            text("UPDATE platform.accounts SET platform_identifier = :p WHERE id = :id"),
            {"p": f"PID-{token}", "id": column_account},
        )

    link = await _search(service_session, ca_integration_uid, f"msp{token}")
    uid = await _search(service_session, ca_integration_uid, account_uid)
    column = await _search(service_session, ca_integration_uid, f"PID-{token}")

    assert [r.contact_uid for r in link.items] == [by_link_uid]
    assert [r.contact_uid for r in uid.items] == [by_uid_uid]
    assert column.total == 0 and column.items == []


@pytest.mark.asyncio
async def test_clients_table_search_total_and_paging_count_the_matches_not_the_book(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    token = uuid.uuid4().hex[:8]
    for i in range(3):
        contact_id, _ = _insert_contact(
            super_engine, ca_tenant_id, first_name="Match", surname=f"{token}{i}"
        )
        _insert_account(super_engine, ca_tenant_id, contact_id)
    for _ in range(2):
        contact_id, _ = _insert_contact(super_engine, ca_tenant_id, surname="Unrelated")
        _insert_account(super_engine, ca_tenant_id, contact_id)

    page1 = await _search(service_session, ca_integration_uid, token, page_size=2)
    page2 = await _search(service_session, ca_integration_uid, token, page=2, page_size=2)

    assert page1.total == page2.total == 3
    assert len(page1.items) == 2
    assert len(page2.items) == 1


@pytest.mark.asyncio
async def test_clients_table_search_treats_like_wildcards_literally(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    """ "%" and "_" are characters to look for, not patterns: otherwise a search box returns the
    whole book for a term nothing contains, which reads as broken data."""
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id, surname="Plain")
    _insert_account(super_engine, ca_tenant_id, contact_id)

    # Each would match "Plain" if it were a pattern: `_` is any one character, `%` any run of them.
    underscore = await _search(service_session, ca_integration_uid, "Pl_in")
    percent = await _search(service_session, ca_integration_uid, "Pl%n")
    bare_percent = await _search(service_session, ca_integration_uid, "%")

    assert underscore.total == 0 and underscore.items == []
    assert percent.total == 0 and percent.items == []
    assert bare_percent.total == 0 and bare_percent.items == []
    # ...and the literal term still finds it: the zeros above are the escaping, not a dead search.
    literal = await _search(service_session, ca_integration_uid, "Plain")
    assert [r.contact_uid for r in literal.items] == [contact_uid]


@pytest.mark.asyncio
async def test_clients_table_search_blank_term_is_the_whole_book(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    _insert_account(super_engine, ca_tenant_id, contact_id)

    for blank in (None, "", "   "):
        result = await _search(service_session, ca_integration_uid, blank)
        assert contact_uid in {r.contact_uid for r in result.items}


@pytest.mark.asyncio
async def test_clients_table_search_never_crosses_into_another_tenant(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_integration_uid: str,
) -> None:
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        other_tenant_id = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"othten_{s}", "a": f"other-{s}", "n": f"Other Tenant {s}"},
            ).scalar_one()
        )
        c.commit()
    contact_id, _ = _insert_contact(super_engine, other_tenant_id, surname=f"Elsewhere{s}")
    account_id, _ = _insert_account(super_engine, other_tenant_id, contact_id, name=f"Acct {s}")
    _insert_account_link(super_engine, other_tenant_id, account_id, external_id=f"NUM{s}")

    for term in (f"Elsewhere{s}", f"Acct {s}", f"NUM{s}"):
        result = await _search(service_session, ca_integration_uid, term)
        assert result.total == 0 and result.items == []


@pytest.mark.asyncio
async def test_clients_table_has_no_existing_link_for_an_unlinked_client(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    _insert_account(super_engine, ca_tenant_id, contact_id)

    rows = await _all_clients_table_rows(service_session, ca_integration_uid)

    row = next(r for r in rows if r.contact_uid == contact_uid)
    assert row.existing_link is None


@pytest.mark.asyncio
async def test_clients_table_embeds_the_existing_link_for_a_linked_client(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id, contact_uid = _insert_contact(
        super_engine, ca_tenant_id, first_name="Chris", surname="Wilson"
    )
    _, account_uid = _insert_account(
        super_engine, ca_tenant_id, contact_id, name="ISA", account_number="SEI-001"
    )

    async def _stub(**_: Any) -> list[dict[str, Any]]:
        return [{"id": "io-1", "name": "Chris Wilson"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)

    # The account has a custodian number, so linking verifies the plan with Intelliflo and pushes
    # that number into it as the policy number.
    pushed: list[tuple[str, str, str]] = []

    async def _plans(client_id_io: str) -> list[dict[str, Any]]:
        return [{"id": "p1", "reference": "ACC-001"}]

    async def _push(client_id_io: str, plan_id_io: str, policy_number: str) -> None:
        pushed.append((client_id_io, plan_id_io, policy_number))

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans)
    monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _push)

    await router.link_client_and_account_to_io(
        router.LinkClientAccountBody(
            contact_uid=contact_uid,
            client_id_io="io-1",
            client_raw={"id": "io-1", "name": "Chris Wilson"},
            account_uid=account_uid,
            plan_id_io="p1",
            plan_raw={"id": "p1", "reference": "ACC-001"},
        ),
        principal=_principal(WRITE),
        session=service_session,
    )

    rows = await _all_clients_table_rows(service_session, ca_integration_uid)

    row = next(r for r in rows if r.contact_uid == contact_uid)
    assert row.existing_link is not None
    assert row.existing_link.client.client_id_io == "io-1"
    assert row.existing_link.account_uid == account_uid
    assert row.existing_link.account_display_name == "ISA (SEI-001)"
    assert row.existing_link.plan.plan_id_io == "p1"
    assert pushed == [("io-1", "p1", "SEI-001")]


@pytest.mark.asyncio
async def test_accounts_for_contact_includes_accounts_of_any_custodian(
    service_session: AsyncSession, super_engine: Engine, ca_tenant_id: int
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    _, sei_uid = _insert_account(super_engine, ca_tenant_id, contact_id, custodian="sei")
    _, other_uid = _insert_account(super_engine, ca_tenant_id, contact_id, custodian="morgan-lloyd")

    result = await router.accounts_for_contact(
        contact_uid, principal=_principal(READ), session=service_session
    )

    assert {r.account_uid for r in result} == {sei_uid, other_uid}


@pytest.mark.asyncio
async def test_clients_table_requires_integrations_read(
    service_session: AsyncSession, ca_integration_uid: str
) -> None:
    with pytest.raises(HTTPException) as exc:
        await router.clients_table(
            integration_uid=ca_integration_uid,
            page=1,
            page_size=10,
            principal=_principal([]),
            session=service_session,
        )
    assert exc.value.status_code == 403


# ============================================================================
# io_clients_for_contact
# ============================================================================


@pytest.mark.asyncio
async def test_io_clients_for_contact_searches_by_ni_number_when_present(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, contact_uid = _insert_contact(
        super_engine,
        ca_tenant_id,
        national_identifier="AB123456C",
        national_identifier_type="nino",
    )
    seen: dict[str, Any] = {}

    async def _stub(**kwargs: Any) -> list[dict[str, Any]]:
        seen.update(kwargs)
        return [{"id": "io-1", "name": "Jane Doe"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)

    result = await router.io_clients_for_contact(
        contact_uid, principal=_principal(READ), session=service_session
    )

    assert seen == {"ni_number": "AB123456C"}
    assert result == [
        router.IoClientOut(client_id_io="io-1", display_name="Jane Doe", raw=result[0].raw)
    ]


@pytest.mark.asyncio
async def test_io_clients_for_contact_searches_only_the_full_name_with_no_ni_number(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no NI number the only search is first+last name together. There is no first-name-only
    or last-name-only fallback: the Intelliflo tenant is shared by every firm, so a single-name
    search would return other firms' clients."""
    _, contact_uid = _insert_contact(super_engine, ca_tenant_id, first_name="Jane", surname="Doe")
    calls: list[dict[str, Any]] = []

    async def _stub(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)

    await router.io_clients_for_contact(
        contact_uid, principal=_principal(READ), session=service_session
    )

    assert calls == [{"first_name": "Jane", "last_name": "Doe"}]


@pytest.mark.asyncio
async def test_io_clients_for_contact_stops_at_the_first_tier_that_finds_something(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The combined first+last tier succeeding must not trigger the looser, narrower tiers --
    those exist only to rescue an otherwise-empty search, not to run unconditionally."""
    _, contact_uid = _insert_contact(super_engine, ca_tenant_id, first_name="Jane", surname="Doe")
    calls: list[dict[str, Any]] = []

    async def _stub(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [{"id": "io-1", "name": "Jane Doe"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)

    await router.io_clients_for_contact(
        contact_uid, principal=_principal(READ), session=service_session
    )

    assert calls == [{"first_name": "Jane", "last_name": "Doe"}]


@pytest.mark.asyncio
async def test_io_clients_for_contact_never_falls_back_to_first_name_alone(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A combined search that finds nothing stays empty. Searching on first name alone would
    return every Jane in the shared Intelliflo tenant -- other firms' clients -- so it is never
    tried, even though it would have found a match here."""
    _, contact_uid = _insert_contact(super_engine, ca_tenant_id, first_name="Jane", surname="Doe")
    calls: list[dict[str, Any]] = []

    async def _stub(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [{"id": "io-1", "name": "Jane"}] if kwargs == {"first_name": "Jane"} else []

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)

    result = await router.io_clients_for_contact(
        contact_uid, principal=_principal(READ), session=service_session
    )

    assert calls == [{"first_name": "Jane", "last_name": "Doe"}]
    assert result == []


@pytest.mark.asyncio
async def test_io_clients_for_contact_ignores_a_non_nino_identifier(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passport number or similar must not be sent to Intelliflo as `ni_number`."""
    _, contact_uid = _insert_contact(
        super_engine,
        ca_tenant_id,
        first_name="Jane",
        surname="Doe",
        national_identifier="P1234567",
        national_identifier_type="passport",
    )
    calls: list[dict[str, Any]] = []

    async def _stub(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return []

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)

    await router.io_clients_for_contact(
        contact_uid, principal=_principal(READ), session=service_session
    )

    assert calls[0] == {"first_name": "Jane", "last_name": "Doe"}
    assert all("ni_number" not in c for c in calls)


@pytest.mark.asyncio
async def test_io_clients_for_contact_404s_for_unknown_uid(service_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await router.io_clients_for_contact(
            "does-not-exist", principal=_principal(READ), session=service_session
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_cls", [IntellifloAuthError, IntellifloApiError])
async def test_io_clients_for_contact_translates_intelliflo_errors_to_502(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
    exc_cls: type[Exception],
) -> None:
    _, contact_uid = _insert_contact(super_engine, ca_tenant_id)

    async def _boom(**_: Any) -> list[dict[str, Any]]:
        raise exc_cls("upstream is down")

    monkeypatch.setattr(intelliflo_client, "get_clients", _boom)

    with pytest.raises(HTTPException) as exc:
        await router.io_clients_for_contact(
            contact_uid, principal=_principal(READ), session=service_session
        )
    assert exc.value.status_code == 502


# ============================================================================
# accounts_for_contact
# ============================================================================


@pytest.mark.asyncio
async def test_accounts_for_contact_returns_only_this_contacts_accounts(
    service_session: AsyncSession, super_engine: Engine, ca_tenant_id: int
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    other_contact_id, _ = _insert_contact(super_engine, ca_tenant_id, first_name="Other")
    _, account_uid = _insert_account(super_engine, ca_tenant_id, contact_id)
    _insert_account(super_engine, ca_tenant_id, other_contact_id)

    result = await router.accounts_for_contact(
        contact_uid, principal=_principal(READ), session=service_session
    )

    assert [r.account_uid for r in result] == [account_uid]


@pytest.mark.asyncio
async def test_accounts_for_contact_display_name_includes_the_account_number(
    service_session: AsyncSession, super_engine: Engine, ca_tenant_id: int
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    _insert_account(super_engine, ca_tenant_id, contact_id, name="ISA", account_number="SEI-00123")

    result = await router.accounts_for_contact(
        contact_uid, principal=_principal(READ), session=service_session
    )

    assert result[0].display_name == "ISA (SEI-00123)"


@pytest.mark.asyncio
async def test_accounts_for_contact_display_name_falls_back_to_uid_without_a_number(
    service_session: AsyncSession, super_engine: Engine, ca_tenant_id: int
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    _, account_uid = _insert_account(super_engine, ca_tenant_id, contact_id, name="ISA")

    result = await router.accounts_for_contact(
        contact_uid, principal=_principal(READ), session=service_session
    )

    assert result[0].display_name == f"ISA ({account_uid})"


@pytest.mark.asyncio
async def test_accounts_for_contact_404s_for_unknown_uid(service_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await router.accounts_for_contact(
            "does-not-exist", principal=_principal(READ), session=service_session
        )
    assert exc.value.status_code == 404


# ============================================================================
# io_accounts_for_client
# ============================================================================


_OURS = {"id": 5261, "name": "Graphene Platforms Limited"}
_SIPP = {"id": 802, "name": "@SIPP"}


@pytest.mark.asyncio
async def test_io_accounts_for_client_keeps_plans_with_no_policy_number(
    service_session: AsyncSession, ca_integration_uid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike the platform portal's dropdown, a plan with no policyNumber yet is a valid pick
    here -- pushing one in is this tab's whole point."""

    async def _stub(client_id_io: str) -> list[dict[str, Any]]:
        return [{"id": "p1", "productProvider": _OURS}]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _stub)

    result = await router.io_accounts_for_client(
        "io-77", ca_integration_uid, principal=_principal(READ), session=service_session
    )

    assert [p.plan_id_io for p in result] == ["p1"]


@pytest.mark.asyncio
async def test_io_accounts_for_client_only_offers_plans_with_no_policy_number(
    service_session: AsyncSession, ca_integration_uid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Null, empty and blank-only all count as "no policy number"; a plan holding one does not."""

    async def _stub(client_id_io: str) -> list[dict[str, Any]]:
        return [
            {"id": "absent", "productProvider": _OURS},
            {"id": "null", "policyNumber": None, "productProvider": _OURS},
            {"id": "empty", "policyNumber": "", "productProvider": _OURS},
            {"id": "blank", "policyNumber": "   ", "productProvider": _OURS},
            {"id": "taken", "policyNumber": "MSP-1", "productProvider": _OURS},
        ]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _stub)

    result = await router.io_accounts_for_client(
        "io-77", ca_integration_uid, principal=_principal(READ), session=service_session
    )

    assert [p.plan_id_io for p in result] == ["absent", "null", "empty", "blank"]


@pytest.mark.asyncio
async def test_io_accounts_for_client_only_offers_plans_under_the_integrations_provider(
    service_session: AsyncSession, ca_integration_uid: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valuations and transactions are pushed under one provider and Intelliflo only matches a plan
    under it, so a plan of any other provider (or with none reported) is not a pick."""

    async def _stub(client_id_io: str) -> list[dict[str, Any]]:
        return [
            {"id": "ours", "productProvider": _OURS},
            {"id": "sipp", "productProvider": _SIPP},
            {"id": "none"},
        ]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _stub)

    result = await router.io_accounts_for_client(
        "io-77", ca_integration_uid, principal=_principal(READ), session=service_session
    )

    assert [p.plan_id_io for p in result] == ["ours"]


@pytest.mark.asyncio
async def test_io_accounts_for_client_does_not_filter_when_the_provider_is_blank(
    service_session: AsyncSession, super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = f"noprov_{uuid.uuid4().hex[:8]}"
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO integrations.platforms "
                "  (uid, platform_name, tenant_id, tenant_name, provider_id) "
                "VALUES (:uid, 'SEI Wealth Platform', :tid, 'Blank provider', '')"
            ),
            {"uid": uid, "tid": f"tenant-{uid}"},
        )
        c.commit()

    async def _stub(client_id_io: str) -> list[dict[str, Any]]:
        return [{"id": "ours", "productProvider": _OURS}, {"id": "sipp", "productProvider": _SIPP}]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _stub)

    result = await router.io_accounts_for_client(
        "io-77", uid, principal=_principal(READ), session=service_session
    )

    assert [p.plan_id_io for p in result] == ["ours", "sipp"]


@pytest.mark.asyncio
async def test_io_accounts_for_client_404s_for_an_unknown_integration(
    service_session: AsyncSession,
) -> None:
    with pytest.raises(HTTPException) as exc:
        await router.io_accounts_for_client(
            "io-77", "does-not-exist", principal=_principal(READ), session=service_session
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_cls", [IntellifloAuthError, IntellifloApiError])
async def test_io_accounts_for_client_translates_intelliflo_errors_to_502(
    service_session: AsyncSession,
    ca_integration_uid: str,
    monkeypatch: pytest.MonkeyPatch,
    exc_cls: type[Exception],
) -> None:
    async def _boom(*_: Any, **__: Any) -> list[dict[str, Any]]:
        raise exc_cls("upstream is down")

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _boom)

    with pytest.raises(HTTPException) as exc:
        await router.io_accounts_for_client(
            "io-77", ca_integration_uid, principal=_principal(READ), session=service_session
        )
    assert exc.value.status_code == 502


# ============================================================================
# link_client_and_account_to_io
# ============================================================================


@pytest.mark.asyncio
async def test_link_client_and_account_refuses_a_plan_that_already_has_a_policy_number(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-step route has no dropdown in front of it, so it enforces the same rule."""
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    account_id, account_uid = _insert_account(super_engine, ca_tenant_id, contact_id)

    async def _clients(**_: Any) -> list[dict[str, Any]]:
        return [{"id": "io-1", "name": "Jane Doe"}]

    async def _plans(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [{"id": "p1", "policyNumber": "SOMEONE-ELSES"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _clients)
    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans)

    with pytest.raises(HTTPException) as refused:
        await router.link_client_and_account_to_io(
            router.LinkClientAccountBody(
                contact_uid=contact_uid,
                client_id_io="io-1",
                client_raw={"id": "io-1"},
                account_uid=account_uid,
                plan_id_io="p1",
                plan_raw={"id": "p1"},
            ),
            principal=_principal(WRITE),
            session=service_session,
        )

    assert refused.value.status_code == 409
    assert not await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
async def test_link_client_and_account_writes_both_links(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    _, account_uid = _insert_account(super_engine, ca_tenant_id, contact_id)

    async def _stub(**_: Any) -> list[dict[str, Any]]:
        return [{"id": "io-1", "name": "Jane Doe"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)

    # The plan is verified against the client's own plans, whether or not the account has a custody
    # number to push.
    async def _plans(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [{"id": "p1", "reference": "ACC-001"}]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans)

    body = router.LinkClientAccountBody(
        contact_uid=contact_uid,
        client_id_io="io-1",
        client_raw={"id": "io-1", "name": "Jane Doe"},
        account_uid=account_uid,
        plan_id_io="p1",
        plan_raw={"id": "p1", "reference": "ACC-001"},
    )

    result = await router.link_client_and_account_to_io(
        body, principal=_principal(WRITE), session=service_session
    )

    assert result.client.client_id_io == "io-1"
    assert result.plan.plan_id_io == "p1"

    contact_row = (
        (
            await service_session.execute(
                text(
                    "SELECT external_id FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'contact' AND entity_id = :cid "
                    "  AND provider = 'intelliflo' AND link_type = 'primary'"
                ),
                {"t": ca_tenant_id, "cid": contact_id},
            )
        )
        .mappings()
        .one()
    )
    assert contact_row["external_id"] == "io-1"


@pytest.mark.asyncio
async def test_link_client_and_account_updates_the_same_row_not_a_new_one(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-linking the same client/account must UPDATE the existing platform.links rows, never
    insert a second one -- the exact requirement the upsert's unique key already enforces.

    The second pass keeps the Intelliflo CLIENT and changes only the plan: changing the client of
    an account that is still mapped is refused (see the 409 tests below), as it should be."""
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    account_id, account_uid = _insert_account(super_engine, ca_tenant_id, contact_id)

    async def _stub(**_: Any) -> list[dict[str, Any]]:
        return [{"id": "io-1", "name": "Jane Doe"}, {"id": "io-2", "name": "Jane Doe"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)

    async def _plans(client_id_io: str, **_: Any) -> list[dict[str, Any]]:
        return [{"id": "p1"}, {"id": "p2"}]

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans)

    for client_id, plan_id in (("io-1", "p1"), ("io-1", "p2")):
        await router.link_client_and_account_to_io(
            router.LinkClientAccountBody(
                contact_uid=contact_uid,
                client_id_io=client_id,
                client_raw={"id": client_id},
                account_uid=account_uid,
                plan_id_io=plan_id,
                plan_raw={"id": plan_id},
            ),
            principal=_principal(WRITE),
            session=service_session,
        )

    contact_rows = (
        (
            await service_session.execute(
                text(
                    "SELECT external_id FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'contact' AND entity_id = :cid "
                    "  AND provider = 'intelliflo' AND link_type = 'primary'"
                ),
                {"t": ca_tenant_id, "cid": contact_id},
            )
        )
        .mappings()
        .all()
    )
    account_rows = (
        (
            await service_session.execute(
                text(
                    "SELECT external_id FROM platform.links "
                    "WHERE tenant_id = :t AND entity_type = 'account' AND entity_id = :aid "
                    "  AND provider = 'intelliflo' AND link_type = 'primary'"
                ),
                {"t": ca_tenant_id, "aid": account_id},
            )
        )
        .mappings()
        .all()
    )
    assert [dict(r) for r in contact_rows] == [{"external_id": "io-1"}]
    assert [dict(r) for r in account_rows] == [{"external_id": "p2"}]


@pytest.mark.asyncio
async def test_link_client_and_account_404s_for_unknown_contact(
    service_session: AsyncSession,
) -> None:
    body = router.LinkClientAccountBody(
        contact_uid="does-not-exist",
        client_id_io="io-1",
        client_raw={"id": "io-1"},
        account_uid="does-not-exist-either",
        plan_id_io="p1",
        plan_raw={"id": "p1"},
    )
    with pytest.raises(HTTPException) as exc:
        await router.link_client_and_account_to_io(
            body, principal=_principal(WRITE), session=service_session
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_link_client_and_account_400s_when_account_belongs_to_a_different_client(
    service_session: AsyncSession, super_engine: Engine, ca_tenant_id: int
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    other_contact_id, _ = _insert_contact(super_engine, ca_tenant_id, first_name="Other")
    _, other_account_uid = _insert_account(super_engine, ca_tenant_id, other_contact_id)

    body = router.LinkClientAccountBody(
        contact_uid=contact_uid,
        client_id_io="io-1",
        client_raw={"id": "io-1"},
        account_uid=other_account_uid,
        plan_id_io="p1",
        plan_raw={"id": "p1"},
    )
    with pytest.raises(HTTPException) as exc:
        await router.link_client_and_account_to_io(
            body, principal=_principal(WRITE), session=service_session
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_link_client_and_account_rejects_a_client_id_not_among_this_contacts_candidates(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admin tab reuses `crm._upsert_contact_io_link`, which now verifies `client_id_io`
    server-side regardless of caller -- an operator posting an id that contact's own Intelliflo
    search never surfaced must be rejected the same way the platform portal's own write is."""
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    _, account_uid = _insert_account(super_engine, ca_tenant_id, contact_id)

    async def _stub(**_: Any) -> list[dict[str, Any]]:
        return [{"id": "io-999", "name": "Someone Else"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)

    body = router.LinkClientAccountBody(
        contact_uid=contact_uid,
        client_id_io="io-1",
        client_raw={"id": "io-1"},
        account_uid=account_uid,
        plan_id_io="p1",
        plan_raw={"id": "p1"},
    )
    with pytest.raises(HTTPException) as exc:
        await router.link_client_and_account_to_io(
            body, principal=_principal(WRITE), session=service_session
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_link_client_and_account_requires_integrations_write(
    service_session: AsyncSession, super_engine: Engine, ca_tenant_id: int
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    _, account_uid = _insert_account(super_engine, ca_tenant_id, contact_id)
    body = router.LinkClientAccountBody(
        contact_uid=contact_uid,
        client_id_io="io-1",
        client_raw={"id": "io-1"},
        account_uid=account_uid,
        plan_id_io="p1",
        plan_raw={"id": "p1"},
    )
    with pytest.raises(HTTPException) as exc:
        await router.link_client_and_account_to_io(
            body, principal=_principal(READ), session=service_session
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_clients_table_has_no_existing_link_for_an_account_link_with_no_contact_link(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    account_id, _ = _insert_account(super_engine, ca_tenant_id, contact_id)
    # Written directly: the endpoint refuses an account link while the client has no Intelliflo
    # client, but a link can outlive the client's (it is unlinked or re-pointed later).
    await service_session.execute(
        text(
            "INSERT INTO platform.links "
            "  (tenant_id, entity_type, entity_id, provider, link_type, external_id, data) "
            "VALUES (:t, 'account', :aid, 'intelliflo', 'primary', 'p1', "
            '  CAST(\'{"id": "p1"}\' AS jsonb))'
        ),
        {"t": ca_tenant_id, "aid": account_id},
    )

    rows = await _all_clients_table_rows(service_session, ca_integration_uid)

    row = next(r for r in rows if r.contact_uid == contact_uid)
    assert row.existing_link is None


@pytest.mark.asyncio
async def test_clients_table_has_no_existing_link_for_a_contact_link_with_no_account_link(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)

    async def _stub(**_: Any) -> list[dict[str, Any]]:
        return [{"id": "io-1"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _stub)

    await router._upsert_contact_io_link(service_session, ca_tenant_id, contact_id, "io-1")

    rows = await _all_clients_table_rows(service_session, ca_integration_uid)

    row = next(r for r in rows if r.contact_uid == contact_uid)
    assert row.existing_link is None


# ============================================================================
# Unlinking an account, and changing a client's Intelliflo client
# ============================================================================


def _insert_io_links(
    super_engine: Engine,
    tenant_id: int,
    contact_id: int,
    account_id: int,
    *,
    client_id_io: str,
    plan_id_io: str,
    policy_number: str,
) -> None:
    """The two Intelliflo links a Link action writes: the client's, and the account's (whose
    external_id is the policy number, and whose data carries the plan id)."""
    with super_engine.connect() as c:
        for entity_type, entity_id, external_id, data in (
            ("contact", contact_id, client_id_io, {"id": client_id_io}),
            (
                "account",
                account_id,
                policy_number,
                {"id": plan_id_io, "policyNumber": policy_number},
            ),
        ):
            c.execute(
                text(
                    "INSERT INTO platform.links "
                    "  (tenant_id, entity_type, entity_id, provider, link_type, external_id, data) "
                    "VALUES (:t, :et, :eid, 'intelliflo', 'primary', :ext, CAST(:data AS jsonb))"
                ),
                {
                    "t": tenant_id,
                    "et": entity_type,
                    "eid": entity_id,
                    "ext": external_id,
                    "data": json.dumps(data),
                },
            )
        c.commit()


async def _io_account_link_exists(session: AsyncSession, account_id: int) -> bool:
    """Read through the SAME session the code under test wrote with: its changes are uncommitted
    until the test ends, so a separate connection would not see them."""
    return bool(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM platform.links WHERE entity_type = 'account' "
                    "AND entity_id = :a AND provider = 'intelliflo'"
                ),
                {"a": account_id},
            )
        ).scalar_one()
    )


async def _io_client_link(session: AsyncSession, contact_id: int) -> str | None:
    return (
        await session.execute(
            text(
                "SELECT external_id FROM platform.links WHERE entity_type = 'contact' "
                "AND entity_id = :c AND provider = 'intelliflo'"
            ),
            {"c": contact_id},
        )
    ).scalar_one_or_none()


class _IoCalls:
    """Stand-ins for the two Intelliflo calls an unlink makes, recording what was sent."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        plans: list[dict[str, Any]],
        read_error: bool = False,
        write_error: bool = False,
    ) -> None:
        self.reads: list[str] = []
        self.patches: list[tuple[str, str, str]] = []

        async def _plans(client_id_io: str) -> list[dict[str, Any]]:
            self.reads.append(client_id_io)
            if read_error:
                raise IntellifloApiError("GET client plans failed: 503")
            return plans

        async def _patch(client_id_io: str, plan_id_io: str, policy_number: str) -> None:
            if write_error:
                raise IntellifloApiError("PATCH plan failed: 400")
            self.patches.append((client_id_io, plan_id_io, policy_number))

        monkeypatch.setattr(intelliflo_client, "get_client_plans", _plans)
        monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _patch)


def _mapped_account(
    super_engine: Engine, tenant_id: int, *, number: str, client: str = "io-1", plan: str = "p1"
) -> tuple[int, int, str]:
    """A client with one account that carries a custodian number and is Intelliflo-mapped, with the
    policy number pushed (so the account link's external_id IS the custodian number)."""
    contact_id, _ = _insert_contact(super_engine, tenant_id)
    account_id, account_uid = _insert_account(
        super_engine, tenant_id, contact_id, name="ISA", account_number=number
    )
    _insert_io_links(
        super_engine,
        tenant_id,
        contact_id,
        account_id,
        client_id_io=client,
        plan_id_io=plan,
        policy_number=number,
    )
    return contact_id, account_id, account_uid


@pytest.mark.asyncio
async def test_changing_the_client_is_refused_while_an_account_is_still_mapped(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id, account_id, _ = _mapped_account(super_engine, ca_tenant_id, number="MSP-BLOCK1")

    async def _would_match(**_: Any) -> list[dict[str, Any]]:
        return [{"id": "io-2", "name": "Someone"}]  # verification would pass if it were reached

    monkeypatch.setattr(intelliflo_client, "get_clients", _would_match)

    with pytest.raises(HTTPException) as exc:
        await router._upsert_contact_io_link(service_session, ca_tenant_id, contact_id, "io-2")

    assert exc.value.status_code == 409
    assert "Account mapping exists" in exc.value.detail
    assert (
        "MSP-BLOCK1" in exc.value.detail
    )  # says WHICH accounts, so the operator knows what to unlink
    assert await _io_client_link(service_session, contact_id) == "io-1"  # nothing changed
    assert await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
async def test_relinking_the_same_intelliflo_client_is_allowed_with_accounts_mapped(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id, account_id, _ = _mapped_account(super_engine, ca_tenant_id, number="MSP-SAME1")

    async def _same(**_: Any) -> list[dict[str, Any]]:
        return [{"id": "io-1", "name": "Same Client"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _same)

    out = await router._upsert_contact_io_link(service_session, ca_tenant_id, contact_id, "io-1")

    assert out.client_id_io == "io-1"
    assert await _io_account_link_exists(
        service_session, account_id
    )  # the account link is untouched


@pytest.mark.asyncio
async def test_changing_the_client_works_once_its_accounts_are_unlinked(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id, account_id, account_uid = _mapped_account(
        super_engine, ca_tenant_id, number="MSP-FLOW1"
    )
    _IoCalls(monkeypatch, plans=[{"id": "p1", "policyNumber": "MSP-FLOW1"}])

    async def _new_client(**_: Any) -> list[dict[str, Any]]:
        return [{"id": "io-2", "name": "New Client"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _new_client)
    with pytest.raises(HTTPException):  # blocked first
        await router._upsert_contact_io_link(service_session, ca_tenant_id, contact_id, "io-2")

    await router.unlink_account_from_io(
        account_uid, None, principal=_principal(WRITE), session=service_session
    )
    await router._upsert_contact_io_link(service_session, ca_tenant_id, contact_id, "io-2")

    assert await _io_client_link(service_session, contact_id) == "io-2"


@pytest.mark.asyncio
async def test_unlink_blanks_the_plans_policy_number_then_removes_the_link(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, account_id, account_uid = _mapped_account(super_engine, ca_tenant_id, number="MSP-UL1")
    io = _IoCalls(monkeypatch, plans=[{"id": "p1", "policyNumber": "MSP-UL1"}])

    out = await router.unlink_account_from_io(
        account_uid, None, principal=_principal(WRITE), session=service_session
    )

    assert io.patches == [("io-1", "p1", "")]
    assert out.unlinked and out.policy_number_cleared and out.plan_id_io == "p1"
    assert not await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
async def test_unlink_leaves_a_policy_number_that_was_changed_in_intelliflo(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Someone edited the plan in Intelliflo since the link. Blanking would overwrite their edit."""
    _, account_id, account_uid = _mapped_account(super_engine, ca_tenant_id, number="MSP-UL2")
    io = _IoCalls(monkeypatch, plans=[{"id": "p1", "policyNumber": "EDITED-BY-HAND"}])

    out = await router.unlink_account_from_io(
        account_uid, None, principal=_principal(WRITE), session=service_session
    )

    assert io.patches == []
    assert out.unlinked and not out.policy_number_cleared
    assert "EDITED-BY-HAND" in (out.note or "")
    assert not await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
async def test_unlink_treats_a_plan_gone_from_intelliflo_as_nothing_to_clear(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, account_id, account_uid = _mapped_account(super_engine, ca_tenant_id, number="MSP-UL3")
    io = _IoCalls(monkeypatch, plans=[])

    out = await router.unlink_account_from_io(
        account_uid, None, principal=_principal(WRITE), session=service_session
    )

    assert io.patches == [] and out.unlinked and not out.policy_number_cleared
    assert not await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
async def test_unlink_never_blanks_a_policy_number_this_link_did_not_set(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A link made WITHOUT the push keeps the plan's OWN number as its external_id. That number is
    the plan's, not ours: it must survive the unlink."""
    contact_id, _ = _insert_contact(super_engine, ca_tenant_id)
    account_id, account_uid = _insert_account(
        super_engine, ca_tenant_id, contact_id, account_number="MSP-UL4"
    )
    _insert_io_links(
        super_engine, ca_tenant_id, contact_id, account_id,
        client_id_io="io-1", plan_id_io="p1", policy_number="THE-PLANS-OWN-NUMBER",
    )  # fmt: skip
    io = _IoCalls(monkeypatch, plans=[{"id": "p1", "policyNumber": "THE-PLANS-OWN-NUMBER"}])

    out = await router.unlink_account_from_io(
        account_uid, None, principal=_principal(WRITE), session=service_session
    )

    assert io.reads == [] and io.patches == []  # Intelliflo was not even asked
    assert out.unlinked and not out.policy_number_cleared
    assert not await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["read", "write"])
async def test_unlink_keeps_the_link_when_intelliflo_fails(
    failing: str,
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Intelliflo first, then the local row: a failure leaves both sides agreeing it is mapped."""
    _, account_id, account_uid = _mapped_account(
        super_engine, ca_tenant_id, number=f"MSP-UL5{failing[0].upper()}"
    )
    _IoCalls(
        monkeypatch,
        plans=[{"id": "p1", "policyNumber": f"MSP-UL5{failing[0].upper()}"}],
        read_error=failing == "read",
        write_error=failing == "write",
    )

    with pytest.raises(HTTPException) as exc:
        await router.unlink_account_from_io(
            account_uid, None, principal=_principal(WRITE), session=service_session
        )

    assert exc.value.status_code == 502 and "link was kept" in exc.value.detail
    assert await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
async def test_unlink_is_idempotent(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, account_uid = _mapped_account(super_engine, ca_tenant_id, number="MSP-UL6")
    _IoCalls(monkeypatch, plans=[{"id": "p1", "policyNumber": "MSP-UL6"}])
    call = router.unlink_account_from_io

    first = await call(account_uid, None, principal=_principal(WRITE), session=service_session)
    second = await call(account_uid, None, principal=_principal(WRITE), session=service_session)

    assert first.unlinked and not second.unlinked


@pytest.mark.asyncio
async def test_unlink_can_skip_intelliflo_for_a_plan_it_will_not_let_us_blank(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, account_id, account_uid = _mapped_account(super_engine, ca_tenant_id, number="MSP-UL7")
    io = _IoCalls(monkeypatch, plans=[], write_error=True, read_error=True)

    out = await router.unlink_account_from_io(
        account_uid,
        router.UnlinkAccountBody(skip_intelliflo=True),
        principal=_principal(WRITE),
        session=service_session,
    )

    assert io.reads == [] and io.patches == []
    assert out.unlinked and not out.policy_number_cleared
    assert not await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
async def test_unlink_touches_only_the_one_account(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another account of the same client, and another tenant's account on a plan of its own (one
    plan is linked to one account across tenants, so it cannot be the same plan id)."""
    contact_id, account_id, account_uid = _mapped_account(
        super_engine, ca_tenant_id, number="MSP-UL8"
    )
    sibling_id, _ = _insert_account(
        super_engine, ca_tenant_id, contact_id, name="GIA", account_number="MSP-UL8B"
    )
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO platform.links (tenant_id, entity_type, entity_id, provider, "
                "link_type, external_id, data) VALUES (:t, 'account', :a, 'intelliflo', "
                "'primary', 'MSP-UL8B', CAST(:d AS jsonb))"
            ),
            {"t": ca_tenant_id, "a": sibling_id, "d": json.dumps({"id": "p2"})},
        )
        c.commit()
    other_tenant = _insert_tenant(super_engine)
    other_contact, _ = _insert_contact(super_engine, other_tenant)
    other_account, _ = _insert_account(
        super_engine, other_tenant, other_contact, account_number="MSP-UL8"
    )
    _insert_io_links(
        super_engine, other_tenant, other_contact, other_account,
        client_id_io="io-9", plan_id_io="p3", policy_number="MSP-UL8",
    )  # fmt: skip
    _IoCalls(monkeypatch, plans=[{"id": "p1", "policyNumber": "MSP-UL8"}])

    await router.unlink_account_from_io(
        account_uid, None, principal=_principal(WRITE), session=service_session
    )

    assert not await _io_account_link_exists(service_session, account_id)
    assert await _io_account_link_exists(service_session, sibling_id)
    assert await _io_account_link_exists(service_session, other_account)


@pytest.mark.asyncio
async def test_unlink_requires_integrations_write_and_a_known_account(
    service_session: AsyncSession,
) -> None:
    with pytest.raises(HTTPException) as denied:
        await router.unlink_account_from_io(
            "any", None, principal=_principal(READ), session=service_session
        )
    with pytest.raises(HTTPException) as missing:
        await router.unlink_account_from_io(
            "no-such-account", None, principal=_principal(WRITE), session=service_session
        )

    assert denied.value.status_code == 403
    assert missing.value.status_code == 404


# ============================================================================
# Unlinking a client from its Intelliflo client
# ============================================================================


def _contact_uid(super_engine: Engine, contact_id: int) -> str:
    with super_engine.connect() as c:
        return str(
            c.execute(
                text("SELECT uid FROM crm.contacts WHERE id = :c"), {"c": contact_id}
            ).scalar_one()
        )


def _insert_client_link(
    super_engine: Engine, tenant_id: int, contact_id: int, client_id_io: str = "io-1"
) -> None:
    """A client linked to its Intelliflo client, with no account mapped yet."""
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, link_type, external_id, data) "
                "VALUES (:t, 'contact', :c, 'intelliflo', 'primary', :ext, CAST(:data AS jsonb))"
            ),
            {
                "t": tenant_id,
                "c": contact_id,
                "ext": client_id_io,
                "data": json.dumps({"id": client_id_io}),
            },
        )
        c.commit()


@pytest.mark.asyncio
async def test_unlinking_a_client_removes_its_intelliflo_link_and_sends_nothing_to_intelliflo(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    _insert_client_link(super_engine, ca_tenant_id, contact_id, "io-9")

    async def _boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("a client unlink must not call Intelliflo")

    monkeypatch.setattr(intelliflo_client, "get_client_plans", _boom)
    monkeypatch.setattr(intelliflo_client, "update_plan_policy_number", _boom)
    monkeypatch.setattr(intelliflo_client, "get_clients", _boom)

    out = await router.unlink_client_from_io(
        contact_uid, principal=_principal(WRITE), session=service_session
    )

    assert out.unlinked and out.client_id_io == "io-9"
    assert await _io_client_link(service_session, contact_id) is None


@pytest.mark.asyncio
async def test_unlinking_a_client_is_refused_while_an_account_is_still_mapped(
    service_session: AsyncSession, super_engine: Engine, ca_tenant_id: int
) -> None:
    contact_id, account_id, _ = _mapped_account(super_engine, ca_tenant_id, number="MSP-CU1")

    with pytest.raises(HTTPException) as refused:
        await router.unlink_client_from_io(
            _contact_uid(super_engine, contact_id),
            principal=_principal(WRITE),
            session=service_session,
        )

    assert refused.value.status_code == 409
    assert "Unlink the accounts" in str(refused.value.detail)
    assert "MSP-CU1" in str(refused.value.detail)
    # Nothing was removed: the client link and the account link are both still there.
    assert await _io_client_link(service_session, contact_id) == "io-1"
    assert await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
async def test_a_client_can_be_unlinked_once_its_accounts_are_unlinked(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id, account_id, account_uid = _mapped_account(
        super_engine, ca_tenant_id, number="MSP-CU2"
    )
    _IoCalls(monkeypatch, plans=[{"id": "p1", "policyNumber": "MSP-CU2"}])
    await router.unlink_account_from_io(
        account_uid, None, principal=_principal(WRITE), session=service_session
    )

    out = await router.unlink_client_from_io(
        _contact_uid(super_engine, contact_id),
        principal=_principal(WRITE),
        session=service_session,
    )

    assert out.unlinked
    assert await _io_client_link(service_session, contact_id) is None
    assert not await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
async def test_unlinking_a_client_touches_only_that_clients_link(
    service_session: AsyncSession, super_engine: Engine, ca_tenant_id: int
) -> None:
    first_id, first_uid = _insert_contact(super_engine, ca_tenant_id)
    second_id, _ = _insert_contact(super_engine, ca_tenant_id, first_name="John", surname="Roe")
    _insert_client_link(super_engine, ca_tenant_id, first_id, "io-1")
    _insert_client_link(super_engine, ca_tenant_id, second_id, "io-2")

    await router.unlink_client_from_io(
        first_uid, principal=_principal(WRITE), session=service_session
    )

    assert await _io_client_link(service_session, first_id) is None
    assert await _io_client_link(service_session, second_id) == "io-2"


@pytest.mark.asyncio
async def test_unlinking_a_client_with_no_link_is_a_no_op(
    service_session: AsyncSession, super_engine: Engine, ca_tenant_id: int
) -> None:
    _, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    call = router.unlink_client_from_io

    first = await call(contact_uid, principal=_principal(WRITE), session=service_session)
    second = await call(contact_uid, principal=_principal(WRITE), session=service_session)

    assert first.unlinked is False and second.unlinked is False


@pytest.mark.asyncio
async def test_unlinking_a_client_requires_integrations_write_and_a_known_client(
    service_session: AsyncSession,
) -> None:
    with pytest.raises(HTTPException) as denied:
        await router.unlink_client_from_io(
            "any", principal=_principal(READ), session=service_session
        )
    with pytest.raises(HTTPException) as missing:
        await router.unlink_client_from_io(
            "no-such-client", principal=_principal(WRITE), session=service_session
        )

    assert denied.value.status_code == 403
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_a_plan_cannot_be_linked_to_two_accounts(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id, account_id, _ = _mapped_account(super_engine, ca_tenant_id, number="MSP-PL1")
    second_id, _ = _insert_account(
        super_engine, ca_tenant_id, contact_id, name="GIA", account_number="MSP-PL2"
    )
    _IoCalls(monkeypatch, plans=[{"id": "p1", "policyNumber": "MSP-PL1"}])

    with pytest.raises(HTTPException) as exc:
        await router._upsert_account_io_link(
            service_session, ca_tenant_id, second_id, "p1", {"id": "p1"}
        )
    assert exc.value.status_code == 409 and "already linked" in exc.value.detail

    # The same account re-linking to its own plan is not a conflict.
    await router._upsert_account_io_link(
        service_session, ca_tenant_id, account_id, "p1", {"id": "p1"}
    )


# ============================================================================
# The split tabs: accounts_table, link_client_to_io, link_account_plan
# ============================================================================


async def _accounts(
    session: AsyncSession, integration_uid: str, **kw: Any
) -> router.AccountsTablePageOut:
    return await router.accounts_table(
        integration_uid=integration_uid,
        page=kw.get("page", 1),
        page_size=kw.get("page_size", 10),
        search=kw.get("search"),
        unlinked_only=kw.get("unlinked_only", False),
        principal=_principal(READ),
        session=session,
    )


def _map_client_only(super_engine: Engine, tenant_id: int, contact_id: int, client: str) -> None:
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO platform.links (tenant_id, entity_type, entity_id, provider, "
                "link_type, external_id, data) VALUES (:t, 'contact', :c, 'intelliflo', "
                "'primary', :ext, CAST(:d AS jsonb))"
            ),
            {"t": tenant_id, "c": contact_id, "ext": client, "d": json.dumps({"id": client})},
        )
        c.commit()


@pytest.mark.asyncio
async def test_accounts_table_lists_one_row_per_account_of_mapped_clients_only(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    mapped, mapped_uid = _insert_contact(super_engine, ca_tenant_id, surname="Mapped")
    _map_client_only(super_engine, ca_tenant_id, mapped, "io-1")
    _, first_uid = _insert_account(
        super_engine, ca_tenant_id, mapped, name="ISA", account_number="MSP-T1"
    )
    _, second_uid = _insert_account(
        super_engine, ca_tenant_id, mapped, name="GIA", account_number="MSP-T2"
    )
    unmapped, _ = _insert_contact(super_engine, ca_tenant_id, surname="Unmapped")
    _insert_account(super_engine, ca_tenant_id, unmapped, name="SIPP", account_number="MSP-T3")

    page = await _accounts(service_session, ca_integration_uid)

    assert {r.account_uid for r in page.items} == {first_uid, second_uid}  # a row per ACCOUNT
    assert all(r.contact_uid == mapped_uid and r.client_id_io == "io-1" for r in page.items)
    assert page.total == 2
    assert (
        page.accounts_awaiting_client == 1
    )  # the unmapped client's account is counted, not listed
    assert {r.account_display_name for r in page.items} == {"ISA (MSP-T1)", "GIA (MSP-T2)"}


@pytest.mark.asyncio
async def test_accounts_table_shows_each_accounts_own_plan_and_filters_unlinked(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    contact_id, _ = _insert_contact(super_engine, ca_tenant_id)
    linked_id, linked_uid = _insert_account(
        super_engine, ca_tenant_id, contact_id, name="ISA", account_number="MSP-F1"
    )
    _insert_io_links(
        super_engine, ca_tenant_id, contact_id, linked_id,
        client_id_io="io-1", plan_id_io="p1", policy_number="MSP-F1",
    )  # fmt: skip
    _, free_uid = _insert_account(
        super_engine, ca_tenant_id, contact_id, name="GIA", account_number="MSP-F2"
    )

    everything = await _accounts(service_session, ca_integration_uid)
    unlinked = await _accounts(service_session, ca_integration_uid, unlinked_only=True)

    by_uid = {r.account_uid: r for r in everything.items}
    assert by_uid[linked_uid].plan is not None and by_uid[linked_uid].plan.plan_id_io == "p1"
    assert by_uid[free_uid].plan is None
    assert [r.account_uid for r in unlinked.items] == [free_uid] and unlinked.total == 1


@pytest.mark.asyncio
async def test_accounts_table_search_and_tenant_scope(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    token = uuid.uuid4().hex[:8]
    mine, _ = _insert_contact(super_engine, ca_tenant_id, surname=f"Quill{token}")
    _map_client_only(super_engine, ca_tenant_id, mine, "io-1")
    _, hit_uid = _insert_account(
        super_engine, ca_tenant_id, mine, name="ISA", account_number=f"MSP{token}"
    )
    other_tenant = _insert_tenant(super_engine)
    theirs, _ = _insert_contact(super_engine, other_tenant, surname=f"Quill{token}")
    _map_client_only(super_engine, other_tenant, theirs, "io-9")
    _insert_account(super_engine, other_tenant, theirs, name="ISA", account_number=f"MSP{token}")

    by_client = await _accounts(service_session, ca_integration_uid, search=f"quill{token}")
    by_number = await _accounts(service_session, ca_integration_uid, search=f"msp{token}")
    nothing = await _accounts(service_session, ca_integration_uid, search="%")

    assert [r.account_uid for r in by_client.items] == [hit_uid]  # never the other tenant's
    assert [r.account_uid for r in by_number.items] == [hit_uid]
    assert nothing.total == 0  # the wildcard is a character, not a pattern


@pytest.mark.asyncio
async def test_clients_table_reports_the_client_link_and_how_many_accounts_are_mapped(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
) -> None:
    contact_id, contact_uid = _insert_contact(super_engine, ca_tenant_id)
    account_id, _ = _insert_account(super_engine, ca_tenant_id, contact_id, account_number="MSP-C1")
    _insert_io_links(
        super_engine, ca_tenant_id, contact_id, account_id,
        client_id_io="io-1", plan_id_io="p1", policy_number="MSP-C1",
    )  # fmt: skip
    _insert_account(super_engine, ca_tenant_id, contact_id, account_number="MSP-C2")  # not linked
    bare, bare_uid = _insert_contact(super_engine, ca_tenant_id)

    rows = {
        r.contact_uid: r for r in await _all_clients_table_rows(service_session, ca_integration_uid)
    }

    assert (
        rows[contact_uid].io_client is not None
        and rows[contact_uid].io_client.client_id_io == "io-1"
    )
    assert rows[contact_uid].linked_account_count == 1
    assert rows[bare_uid].io_client is None and rows[bare_uid].linked_account_count == 0


@pytest.mark.asyncio
async def test_link_client_to_io_refuses_a_change_while_accounts_are_mapped(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id, _ = _insert_contact(super_engine, ca_tenant_id)
    account_id, _ = _insert_account(super_engine, ca_tenant_id, contact_id, account_number="MSP-L1")
    _insert_io_links(
        super_engine, ca_tenant_id, contact_id, account_id,
        client_id_io="io-1", plan_id_io="p1", policy_number="MSP-L1",
    )  # fmt: skip
    uid = (
        await service_session.execute(
            text("SELECT uid FROM crm.contacts WHERE id = :i"), {"i": contact_id}
        )
    ).scalar_one()

    async def _clients(**_: Any) -> list[dict[str, Any]]:
        return [{"id": "io-2", "name": "Other"}]

    monkeypatch.setattr(intelliflo_client, "get_clients", _clients)

    with pytest.raises(HTTPException) as exc:
        await router.link_client_to_io(
            router.ClientLinkBody(contact_uid=uid, client_id_io="io-2"),
            principal=_principal(WRITE),
            session=service_session,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_link_account_plan_needs_a_mapped_client_and_a_plan_of_that_client(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contact_id, _ = _insert_contact(super_engine, ca_tenant_id)
    _, account_uid = _insert_account(
        super_engine, ca_tenant_id, contact_id, name="ISA", account_number="MSP-K1"
    )
    io = _IoCalls(monkeypatch, plans=[{"id": "p1", "policyNumber": None, "productProvider": _OURS}])
    body = router.AccountLinkBody(
        integration_uid=ca_integration_uid, account_uid=account_uid, plan_id_io="p1"
    )

    # 1. the client is not mapped yet
    with pytest.raises(HTTPException) as unmapped:
        await router.link_account_plan(body, principal=_principal(WRITE), session=service_session)
    assert unmapped.value.status_code == 409 and io.reads == []

    # 2. mapped, but the plan is not one of that client's
    _map_client_only(super_engine, ca_tenant_id, contact_id, "io-1")
    with pytest.raises(HTTPException) as foreign:
        await router.link_account_plan(
            router.AccountLinkBody(
                integration_uid=ca_integration_uid,
                account_uid=account_uid,
                plan_id_io="someone-elses",
            ),
            principal=_principal(WRITE),
            session=service_session,
        )
    assert foreign.value.status_code == 400

    # 3. a plan of the client: linked, with the custodian number pushed into it, using ONE read
    io.reads.clear()
    out = await router.link_account_plan(body, principal=_principal(WRITE), session=service_session)
    assert out.plan_id_io == "p1" and out.policy_number == "MSP-K1"
    assert io.patches == [("io-1", "p1", "MSP-K1")]
    assert io.reads == ["io-1"]  # verified once in the endpoint, not again in the helper


@pytest.mark.asyncio
async def test_link_account_plan_refuses_a_plan_that_already_has_a_policy_number(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dropdown hides such a plan, but anyone can send a plan id to the API, so the endpoint
    says no too, and nothing is written to Intelliflo or kept locally."""
    contact_id, _ = _insert_contact(super_engine, ca_tenant_id)
    account_id, account_uid = _insert_account(
        super_engine, ca_tenant_id, contact_id, name="ISA", account_number="MSP-T1"
    )
    _map_client_only(super_engine, ca_tenant_id, contact_id, "io-1")
    io = _IoCalls(
        monkeypatch, plans=[{"id": "p1", "policyNumber": "SOMEONE-ELSES", "productProvider": _OURS}]
    )

    with pytest.raises(HTTPException) as refused:
        await router.link_account_plan(
            router.AccountLinkBody(
                integration_uid=ca_integration_uid, account_uid=account_uid, plan_id_io="p1"
            ),
            principal=_principal(WRITE),
            session=service_session,
        )

    assert refused.value.status_code == 409 and "SOMEONE-ELSES" in str(refused.value.detail)
    assert io.patches == []
    assert not await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
async def test_link_account_plan_accepts_a_plan_holding_this_accounts_own_number(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a link the plan holds the account's number, so a retry or re-link must still pass."""
    contact_id, _ = _insert_contact(super_engine, ca_tenant_id)
    account_id, account_uid = _insert_account(
        super_engine, ca_tenant_id, contact_id, name="ISA", account_number="MSP-T2"
    )
    _map_client_only(super_engine, ca_tenant_id, contact_id, "io-1")
    _IoCalls(monkeypatch, plans=[{"id": "p1", "policyNumber": "MSP-T2", "productProvider": _OURS}])

    out = await router.link_account_plan(
        router.AccountLinkBody(
            integration_uid=ca_integration_uid, account_uid=account_uid, plan_id_io="p1"
        ),
        principal=_principal(WRITE),
        session=service_session,
    )

    assert out.plan_id_io == "p1"
    assert await _io_account_link_exists(service_session, account_id)


@pytest.mark.asyncio
async def test_link_account_plan_refuses_a_plan_under_another_provider(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    ca_integration_uid: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plan under a provider the integration does not push to can be picked in IO but never
    receives a valuation or a transaction: refuse it, and write nothing."""
    contact_id, _ = _insert_contact(super_engine, ca_tenant_id)
    _, account_uid = _insert_account(
        super_engine, ca_tenant_id, contact_id, name="ISA", account_number="MSP-K9"
    )
    _map_client_only(super_engine, ca_tenant_id, contact_id, "io-1")
    io = _IoCalls(monkeypatch, plans=[{"id": "p1", "productProvider": _SIPP}])

    with pytest.raises(HTTPException) as exc:
        await router.link_account_plan(
            router.AccountLinkBody(
                integration_uid=ca_integration_uid, account_uid=account_uid, plan_id_io="p1"
            ),
            principal=_principal(WRITE),
            session=service_session,
        )

    assert exc.value.status_code == 409
    assert "@SIPP" in exc.value.detail and "5261" in exc.value.detail
    assert io.patches == []  # nothing was pushed into the plan


@pytest.mark.asyncio
async def test_the_new_link_endpoints_require_integrations_write(
    service_session: AsyncSession,
) -> None:
    for func, body in (
        (router.link_client_to_io, router.ClientLinkBody(contact_uid="x", client_id_io="1")),
        (
            router.link_account_plan,
            router.AccountLinkBody(integration_uid="x", account_uid="x", plan_id_io="1"),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await func(
                body,
                principal=_principal(READ),
                session=service_session,
            )
        assert exc.value.status_code == 403


def test_a_company_is_searched_by_its_corporate_name_only() -> None:
    """IO keeps a company under `corporate.name`; `person.lastName` can never match it, so none of
    the person tiers (NINO, first/last name) is built for an organisation."""
    from app.routers.crm import _io_client_search_tiers

    org = {
        "type": "organisation",
        "name": "Daniscan Services Limited",
        "first_name": None,
        "surname": "Daniscan Services Limited",
        "national_identifier": None,
        "national_identifier_type": None,
    }
    assert _io_client_search_tiers(org) == [{"corporate_name": "Daniscan Services Limited"}]
    assert _io_client_search_tiers({**org, "name": None}) == [
        {"corporate_name": "Daniscan Services Limited"}
    ]
    assert _io_client_search_tiers({**org, "name": None, "surname": None}) == []

    person = {**org, "type": "individual", "first_name": "Gary", "surname": "Giles", "name": None}
    assert _io_client_search_tiers(person) == [{"first_name": "Gary", "last_name": "Giles"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ReadTimeout("t"), httpx.ConnectError("refused")])
async def test_the_account_page_plans_degrade_to_empty_when_intelliflo_cannot_be_reached(
    service_session: AsyncSession,
    super_engine: Engine,
    ca_tenant_id: int,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    """Through the REAL client, not a stub: an IO timeout or refused connection must not fail the
    whole account record (the "IO Plans" card is a preview-only read)."""
    from app.routers.crm import _io_plans_for_contact

    contact_id, _ = _insert_contact(super_engine, ca_tenant_id)
    _map_client_only(super_engine, ca_tenant_id, contact_id, "io-1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/connect/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        raise failure

    def _make(settings: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=settings.timeout)

    intelliflo_client.clear_token_cache()
    monkeypatch.setattr(intelliflo_client, "_http_client", _make)

    assert await _io_plans_for_contact(service_session, contact_id) == []
