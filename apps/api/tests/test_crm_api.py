"""Endpoint-behaviour tests for the /crm read surface.

Drives the router coroutines against a real ``authenticated`` session (role + claims minted by
``auth.resolve_claims``, exactly like db.py::tenant_session), so RLS decides visibility -- the
same harness as ``test_catalog_api.py``.

What is pinned here:

  * AUM excludes ``draft`` and ``closed`` accounts (they used to be summed into every figure);
  * every money figure carries its provenance (``value_as_of`` + ``value_source``) and the
    overview returns **no** performance series (it used to rescale the lake's series so its last
    point equalled AUM -- a manufactured curve);
  * the client's adviser is ``crm.client_advisers.role = 'adviser'``, not an alphabetical name;
  * the client portal's figures carry their provenance date: /crm/home and /crm/context stamp
    ``as_of`` (the max ``_VALUED_AT`` over VALUED accounts -- home also folds in the last series
    point), each home wrapper carries its own ``value_as_of``, and ``performance_period`` states
    the chart's basis -- all null for an accountless book, never a guess;
  * a book-scoped adviser can read back a client they have just created (the create path must
    write the adviser's book row, or the client is invisible to its own creator);
  * the client record surfaces KYC + related parties and bounds the notes list.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import addressing
from app import contacts as contacts_mod
from app.auth import Principal
from app.config import settings
from app.db import FirmContext
from app.routers import crm as crm_router
from tests.conftest import SECCL_PLATFORM_SQL

# App-only portal (0107): one application client id per portal surface, shared by every fixture
# tenant in this module — resolve_claims matches an app id against the CALLER'S OWN tenant's
# environment row, so reusing the strings across tenants is safe.
_APP_CLIENT = "app_crm_client"
_APP_PLATFORM = "app_crm_platform"


@pytest.mark.asyncio
async def test_account_bank_details_are_refused_when_the_product_disables_them() -> None:
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: False))
    )
    ctx = FirmContext(
        session=session,  # type: ignore[arg-type]
        tenant_id=7,
        principal=Principal(
            user_id="staff",
            organisation_id="firm",
            permissions=["instructions:write"],
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await crm_router.set_account_bank_accounts(
            account_uid="acc_none",
            account_id=11,
            body=crm_router.SetAccountBankAccounts(
                bank_accounts=[
                    {
                        "name": "Junior ISA destination",
                        "sort_code": "112233",
                        "account_number": "12345678",
                    }
                ]
            ),
            ctx=ctx,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Bank details are not allowed for this product."


@asynccontextmanager
async def _authed(
    app_url: str, workos_user: str, perms: list[str], role: str = "adviser"
) -> AsyncIterator[AsyncSession]:
    """A session acting as a request principal -- SET ROLE authenticated + claims minted by
    auth.resolve_claims, exactly like db.py::tenant_session. ``role='client'`` signs in through
    the CLIENT application (0107), so the minted portal claim is 'client' — which empties
    auth.tenant_lineage(), and the caller reaches data only through their contact/account scope."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        resolved = (
            await session.execute(
                text("SELECT auth.resolve_claims(:w, NULL, :app)"),
                {"w": workos_user, "app": _APP_CLIENT if role == "client" else _APP_PLATFORM},
            )
        ).scalar_one()
        claims = {**resolved, "sub": workos_user, "role": role, "permissions": perms}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


@asynccontextmanager
async def _firm(app_url: str) -> AsyncIterator[AsyncSession]:
    """A service_role session -- mirrors db.py::firm_session (the write path)."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session
    await engine.dispose()


@pytest.fixture
def firm(super_engine: Engine) -> dict[str, object]:
    """One firm with a read-all adviser, a book-scoped adviser, two clients and three accounts:
    an active one (£100k), a draft one (£50k) and a closed one (£70k) -- so a test can prove
    which of them AUM counts. The scoped adviser's book is the second client only."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"crmten_{s}", "a": f"crmfirm-{s}", "n": f"CRMFirm {s}"},
            ).scalar_one()
        )
        # App-only portal (0107): the firm's environment declares its portal applications; a
        # login holds a portal via an ACTIVE membership for it (status defaults to 'active').
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  client_portal_client_id, platform_client_id) "
                "VALUES (:t, 'dev', :env, :capp, :papp)"
            ),
            {"t": tenant, "env": f"env_crm_{s}", "capp": _APP_CLIENT, "papp": _APP_PLATFORM},
        )

        def mk_user(tag: str, first: str, last: str) -> tuple[str, str]:
            wos = f"crmwos_{tag}_{s}"
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                        "  first_name, last_name, enabled) "
                        "VALUES (:uid, :t, :email, :wos, :first, :last, true) RETURNING id"
                    ),
                    {
                        "uid": f"crmusr_{tag}_{s}",
                        "t": tenant,
                        "email": f"{tag}_{s}@crmfirm.test",
                        "wos": wos,
                        "first": first,
                        "last": last,
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, 'platform')"
                ),
                {"u": uid, "t": tenant},
            )
            return uid, wos

        # Deliberate name order: "Adele" sorts before "Zoe", so an alphabetical name
        # picks Adele while the adviser role belongs to Zoe.
        all_id, all_wos = mk_user("all", "Zoe", "Lead")
        scoped_id, scoped_wos = mk_user("scoped", "Adele", "Reviewer")

        def mk_contact(tag: str, surname: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                        "VALUES (:t, :uid, 'individual', :sn, 'advised') RETURNING id"
                    ),
                    {"t": tenant, "uid": f"crmcon_{tag}_{s}", "sn": surname},
                ).scalar_one()
            )

        rich = mk_contact("rich", "Ashworth")
        booked = mk_contact("booked", "Beaumont")

        def mk_account(tag: str, contact: int, status: str, value: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO platform.accounts (tenant_id, contact_id, uid, status, value) "
                        "VALUES (:t, :con, :uid, CAST(:st AS platform.account_status), :v) "
                        "RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "con": contact,
                        "uid": f"crmacc_{tag}_{s}",
                        "st": status,
                        "v": value,
                    },
                ).scalar_one()
            )

        active = mk_account("active", rich, "active", "100000.00")
        mk_account("draft", rich, "draft", "50000.00")
        mk_account("closed", rich, "closed", "70000.00")
        mk_account("booked", booked, "active", "25000.00")

        # Provenance: the active account is linked to Seccl, so value_source says 'seccl'.
        c.execute(
            text(
                "INSERT INTO platform.links "
                "(tenant_id, entity_type, entity_id, provider, external_id) "
                "VALUES (:t, 'account', :eid, 'seccl', :ext)"
            ),
            {"t": tenant, "eid": active, "ext": f"SECCL_{s}"},
        )
        # Both are on the rich client's book; Zoe is the adviser (0245: one adviser per contact),
        # Adele just a support role -- so an alphabetical name would pick Adele.
        for adviser, role in ((all_id, "adviser"), (scoped_id, "support")):
            c.execute(
                text(
                    "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                    "VALUES (:t, :con, :a, :r)"
                ),
                {"t": tenant, "con": rich, "a": adviser, "r": role},
            )
        # ...and the scoped adviser also holds the second client (their whole visible book).
        c.execute(
            text(
                "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                "VALUES (:t, :con, :a, 'adviser')"
            ),
            {"t": tenant, "con": booked, "a": scoped_id},
        )
        c.commit()
    return {
        "tenant": tenant,
        "all_wos": all_wos,
        "scoped_wos": scoped_wos,
        "scoped_id": scoped_id,
        "rich": rich,
        "rich_uid": f"crmcon_rich_{s}",
        "booked": booked,
        "booked_uid": f"crmcon_booked_{s}",
        "active_account": active,
        "active_account_uid": f"crmacc_active_{s}",
        "active_ext": f"SECCL_{s}",
    }


@pytest.mark.asyncio
async def test_overview_excludes_draft_and_closed_from_aum(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """AUM is the money the firm actually holds: the draft (£50k) and closed (£70k) accounts
    are excluded, so £100k + £25k = £125k, not £245k."""
    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.overview(session=session)
    assert out["aum"] == pytest.approx(125000.0)
    assert out["accounts"] == 4  # every account is still counted...
    assert out["valued_accounts"] == 2  # ...but only two of them carry value
    assert out["clients_funded"] == 2


@pytest.mark.asyncio
async def test_overview_states_provenance_and_no_series(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """Money carries its as-at + source, and there is no performance series to fabricate."""
    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.overview(session=session)
    assert out["values_as_of"] is not None
    assert out["value_source"] == "seccl"
    assert "series" not in out and "gain" not in out and "gainPct" not in out


@pytest.mark.asyncio
async def test_overview_does_not_return_a_top_teaser(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """The dashboard paints the real book table. Fetching the top six by AUM (with a correlated
    adviser subquery) was paid on every home-page load and never rendered."""
    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.overview(session=session)
    assert "top" not in out


@pytest.mark.asyncio
async def test_list_contacts_values_only_valued_accounts(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """The list's per-client AUM uses the same basis as the dashboard's -- and is not multiplied
    by the number of rows on a client's team (the accounts/advisers joins used to fan out, so this
    client, who has two team rows, reported £200,000)."""
    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_contacts(session=session)
    by_id = {int(r["id"]): r for r in rows}
    rich = by_id[int(firm["rich"])]
    # `advisers` now holds only the role='adviser' row (0245) -- Zoe, not the support row too.
    assert len(rich["advisers"]) == 1
    assert rich["portfolio_value"] == pytest.approx(100000.0)
    assert rich["account_count"] == 3
    assert rich["valued_account_count"] == 1
    assert rich["value_as_of"] is not None


def _state_cash(
    engine: Engine,
    tenant: int,
    account: int,
    cash: float,
    *,
    growth: float | None = 0,
    source: str = "seccl",
    day: str = "2026-03-08",
) -> None:
    """One valuation row: the figure the listings read, instead of a holdings scan."""
    with engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO platform.valuations "
                "(tenant_id, account_id, as_of_date, source_system, market_value, "
                " cash_value, unrealised_gain) "
                "VALUES (:t, :a, CAST(:d AS date), :s, :cash, :cash, :g)"
            ),
            {"t": tenant, "a": account, "d": day, "s": source, "cash": cash, "g": growth},
        )
        c.commit()


@pytest.mark.asyncio
async def test_list_contacts_sorts_by_stated_cash_across_the_whole_book(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """``sort=cash`` orders the whole book, not the page on screen. ``booked`` holds far more
    cash than ``rich``, who sorts first by name, so a page-only sort would put ``rich`` first."""
    tenant = int(firm["tenant"])
    with super_engine.connect() as c:
        booked_account = int(
            c.execute(
                text("SELECT id FROM platform.accounts WHERE tenant_id = :t AND contact_id = :con"),
                {"t": tenant, "con": int(firm["booked"])},
            ).scalar_one()
        )
    _state_cash(super_engine, tenant, booked_account, 9000)
    _state_cash(super_engine, tenant, int(firm["active_account"]), 500)

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        desc = await crm_router.list_contacts(
            sort="cash", dir="desc", limit=10, offset=0, session=session
        )
        asc = await crm_router.list_contacts(
            sort="cash", dir="asc", limit=10, offset=0, session=session
        )
        unpaged = await crm_router.list_contacts(sort="cash", dir="desc", session=session)

    assert isinstance(desc, dict) and isinstance(asc, dict)
    assert isinstance(unpaged, list)
    desc_ids = [int(r["id"]) for r in desc["items"]]
    asc_ids = [int(r["id"]) for r in asc["items"]]
    assert desc_ids == [firm["booked"], firm["rich"]]
    assert asc_ids == [firm["rich"], firm["booked"]]
    assert [int(r["id"]) for r in unpaged] == desc_ids
    # The displayed figure agrees with what it was sorted by -- a page sorted by one source and
    # displaying another would be worse than not sorting at all.
    by_id = {int(r["id"]): r for r in desc["items"]}
    assert by_id[int(firm["booked"])]["cash"] == pytest.approx(9000.0)
    assert by_id[int(firm["rich"])]["cash"] == pytest.approx(500.0)
    assert desc["total"] == 2


@pytest.mark.asyncio
async def test_list_contacts_cash_sort_breaks_ties_by_id_not_by_chance(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """An unstated cash figure is NULL, so a tie is the common case. Without the id tiebreak a
    tied pair on the page boundary can show twice, or not at all, between two requests. ``rich``
    has a stated figure and sorts first; ``booked`` and three further contacts have none, so all
    four tie and must come back in descending id, on one page and when walked in pages of two."""
    tenant = int(firm["tenant"])
    with super_engine.connect() as c:
        tie_ids = [
            int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, surname) "
                        "VALUES (:t, :u, 'individual', :s) RETURNING id"
                    ),
                    {"t": tenant, "u": f"crmcon_tie{i}_{firm['booked_uid'][-8:]}", "s": f"Tie{i}"},
                ).scalar_one()
            )
            for i in range(3)
        ]
        c.commit()
    tied_contacts = sorted([int(firm["booked"]), *tie_ids])
    _state_cash(super_engine, tenant, int(firm["active_account"]), 100)

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        whole = await crm_router.list_contacts(
            sort="cash", dir="desc", limit=10, offset=0, session=session
        )
        assert isinstance(whole, dict)
        # rich (100) first, then the unstated four in DESCENDING id.
        assert [int(r["id"]) for r in whole["items"]] == [
            int(firm["rich"]),
            *sorted(tied_contacts, reverse=True),
        ]

        # Walking the tied group in a DIFFERENT page size must land on the exact same set, with
        # no id repeated and none missing -- the actual, user-visible symptom an unstable
        # tiebreaker produces (a client shown twice, or never shown, while paging).
        seen: list[int] = []
        offset = 0
        while True:
            page = await crm_router.list_contacts(
                sort="cash", dir="desc", limit=2, offset=offset, session=session
            )
            assert isinstance(page, dict)
            if not page["items"]:
                break
            seen.extend(int(r["id"]) for r in page["items"])
            offset += 2
        assert seen == [int(firm["rich"]), *sorted(tied_contacts, reverse=True)]
        assert len(seen) == len(set(seen))  # no id repeated across the walk


@pytest.mark.asyncio
async def test_list_contacts_unpaged_export_honors_cash_sort(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The unpaged export used to return before looking at ``sort``. No ``limit`` takes that
    branch; ``booked`` holds more cash than ``rich``."""
    tenant = int(firm["tenant"])
    with super_engine.connect() as c:
        booked_account = int(
            c.execute(
                text("SELECT id FROM platform.accounts WHERE tenant_id = :t AND contact_id = :con"),
                {"t": tenant, "con": int(firm["booked"])},
            ).scalar_one()
        )
    _state_cash(super_engine, tenant, booked_account, 9000)
    _state_cash(super_engine, tenant, int(firm["active_account"]), 500)

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        desc = await crm_router.list_contacts(sort="cash", dir="desc", session=session)
        asc = await crm_router.list_contacts(sort="cash", dir="asc", session=session)

    assert isinstance(desc, list) and isinstance(asc, list)
    assert [int(r["id"]) for r in desc] == [firm["booked"], firm["rich"]]
    assert [int(r["id"]) for r in asc] == [firm["rich"], firm["booked"]]


@pytest.mark.asyncio
async def test_list_accounts_sorts_by_stated_cash_across_the_whole_book(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The accounts list orders by the stored valuation, across the book. Narrowed to active
    accounts so the draft and closed ones the fixture also creates stay out of the match."""
    tenant = int(firm["tenant"])
    active_account = int(firm["active_account"])
    with super_engine.connect() as c:
        booked_account = int(
            c.execute(
                text("SELECT id FROM platform.accounts WHERE tenant_id = :t AND contact_id = :con"),
                {"t": tenant, "con": int(firm["booked"])},
            ).scalar_one()
        )
    _state_cash(super_engine, tenant, booked_account, 9000)
    _state_cash(super_engine, tenant, active_account, 500)

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        desc = await crm_router.list_accounts(
            status=["active"], sort="cash", dir="desc", limit=10, offset=0, session=session
        )
        asc = await crm_router.list_accounts(
            status=["active"], sort="cash", dir="asc", limit=10, offset=0, session=session
        )

    assert isinstance(desc, dict) and isinstance(asc, dict)
    assert [int(r["id"]) for r in desc["items"]] == [booked_account, active_account]
    assert [int(r["id"]) for r in asc["items"]] == [active_account, booked_account]
    by_id = {int(r["id"]): r for r in desc["items"]}
    assert by_id[booked_account]["cash"] == pytest.approx(9000.0)
    assert by_id[active_account]["cash"] == pytest.approx(500.0)
    assert desc["total"] == 2


@pytest.mark.asyncio
async def test_listing_cash_sums_sources_and_growth_does_not(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """0117, on the listing. The scheme administrator's bank balance adds to the custodian's
    cash; its unrealised gain does not, because that figure is one we sent it. An older
    custodian row is not the latest. A draft account's cash is not the client's."""
    tenant = int(firm["tenant"])
    active = int(firm["active_account"])
    with super_engine.connect() as c:
        draft = int(
            c.execute(
                text(
                    "SELECT id FROM platform.accounts "
                    " WHERE tenant_id = :t AND contact_id = :con AND status = 'draft'"
                ),
                {"t": tenant, "con": int(firm["rich"])},
            ).scalar_one()
        )
    _state_cash(super_engine, tenant, active, 1, growth=1, day="2026-03-01")
    _state_cash(super_engine, tenant, active, 100, growth=40, day="2026-03-08")
    _state_cash(
        super_engine, tenant, active, 25, growth=999, source="morgan-lloyd", day="2026-03-08"
    )
    _state_cash(super_engine, tenant, draft, 5000, growth=5000)

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        accounts = await crm_router.list_accounts(session=session)
        contacts = await crm_router.list_contacts(session=session)
    assert isinstance(accounts, list) and isinstance(contacts, list)
    listed = next(r for r in accounts if int(str(r["id"])) == active)
    assert listed["cash"] == pytest.approx(125)
    assert listed["growth"] == pytest.approx(40)
    client = next(r for r in contacts if int(str(r["id"])) == int(firm["rich"]))
    assert client["cash"] == pytest.approx(125)
    assert client["growth"] == pytest.approx(40)
    unstated = next(r for r in contacts if int(str(r["id"])) == int(firm["booked"]))
    assert unstated["cash"] is None
    assert unstated["growth"] is None


@pytest.mark.asyncio
async def test_the_account_record_shows_the_custodian_cash_the_listing_is_built_from(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The positions file can omit an account. The record used to read that file and show £0,
    while the listing still showed the last cash the custodian stated. The record shows that
    stated cash. An older day is not the balance, and the scheme administrator's bank balance
    is not added here — the record adds it from ``provider_positions``."""
    from app.lake import holdings as lake_holdings

    tenant = int(firm["tenant"])
    active = int(firm["active_account"])
    _state_cash(super_engine, tenant, active, 10, day="2026-10-01")
    _state_cash(super_engine, tenant, active, 1500, day="2026-10-13")
    _state_cash(super_engine, tenant, active, 9999, source="morgan-lloyd", day="2026-10-13")

    async def positions_say_one_pound(
        provider: str, tenant_uid: str, external_account_ids: list[str]
    ) -> list[dict[str, object]]:
        del provider, tenant_uid
        return [
            {
                "account_external_id": external_account_ids[0],
                "klass": "Cash",
                "value": 1.0,
                "growth": 0.0,
            }
        ]

    monkeypatch.setattr(lake_holdings, "firm_holdings_summary", positions_say_one_pound)

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        record = await crm_router.get_account(account_id=active, session=session)
        contact = await crm_router.get_contact(contact_id=int(firm["rich"]), session=session)
    assert record["cash"] == pytest.approx(1500)
    account = next(row for row in contact["accounts"] if int(row["id"]) == active)  # type: ignore[index]
    assert account["cash"] == pytest.approx(1500)


@pytest.mark.asyncio
async def test_listing_growth_uses_an_administrator_figure_when_no_custodian_stated_one(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """A seeded book tagged as the scheme administrator has no custodian valuation. Its stored
    gain is the one the listing shows; it is not added to a custodian gain that does not exist."""
    tenant = int(firm["tenant"])
    with super_engine.connect() as c:
        booked_account = int(
            c.execute(
                text("SELECT id FROM platform.accounts WHERE tenant_id = :t AND contact_id = :con"),
                {"t": tenant, "con": int(firm["booked"])},
            ).scalar_one()
        )
    _state_cash(super_engine, tenant, booked_account, 10, growth=30, source="morgan-lloyd")

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        accounts = await crm_router.list_accounts(session=session)
    assert isinstance(accounts, list)
    listed = next(r for r in accounts if int(str(r["id"])) == booked_account)
    assert listed["cash"] == pytest.approx(10)
    assert listed["growth"] == pytest.approx(30)


@pytest.mark.asyncio
async def test_list_accounts_unpaged_export_honors_cash_sort(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The unpaged accounts export honours ``sort=cash``."""
    tenant = int(firm["tenant"])
    active_account = int(firm["active_account"])
    with super_engine.connect() as c:
        booked_account = int(
            c.execute(
                text("SELECT id FROM platform.accounts WHERE tenant_id = :t AND contact_id = :con"),
                {"t": tenant, "con": int(firm["booked"])},
            ).scalar_one()
        )
    _state_cash(super_engine, tenant, booked_account, 9000)
    _state_cash(super_engine, tenant, active_account, 500)

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        desc = await crm_router.list_accounts(
            status=["active"], sort="cash", dir="desc", session=session
        )
        asc = await crm_router.list_accounts(
            status=["active"], sort="cash", dir="asc", session=session
        )

    assert isinstance(desc, list) and isinstance(asc, list)
    assert [int(r["id"]) for r in desc] == [booked_account, active_account]
    assert [int(r["id"]) for r in asc] == [active_account, booked_account]


class TestAddressingByUid:
    """`GET /crm/{contacts,accounts}/{uid}` -- a record is addressed by its uid, in the API too.

    There used to be a `by-uid` resolve beside the id-addressed detail, because the portals'
    routes carried a uid and these endpoints took an `int`. The path takes the uid now, so the
    resolve is gone and two things have to hold of what replaced it:

      * the resolve runs inside the caller's OWN RLS-bound session, so a uid the caller cannot
        see is a 404 rather than a leak of "that exists, but not for you";
      * an unknown uid is a 404 and not an empty record.
    """

    @pytest.mark.asyncio
    async def test_the_record_is_reached_by_its_uid(
        self, db: dict[str, str], firm: dict[str, object]
    ) -> None:
        async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
            contact = await crm_router.get_contact(
                contact_id=await addressing.book_contact_id(
                    contact_uid=str(firm["rich_uid"]), session=session
                ),
                session=session,
            )
            account = await crm_router.get_account(
                account_id=await addressing.book_account_id(
                    account_uid=str(firm["active_account_uid"]), session=session
                ),
                session=session,
            )
        assert str(contact["contact"]["uid"]) == str(firm["rich_uid"])  # type: ignore[index]
        assert str(account["uid"]) == str(firm["active_account_uid"])

    @pytest.mark.asyncio
    async def test_an_unknown_uid_is_a_404_not_an_empty_record(
        self, db: dict[str, str], firm: dict[str, object]
    ) -> None:
        async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
            for call in (
                addressing.book_contact_id(contact_uid="crmcon_nosuch", session=session),
                addressing.book_account_id(account_uid="crmacc_nosuch", session=session),
            ):
                with pytest.raises(HTTPException) as raised:
                    await call
                assert raised.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_uid_outside_the_caller_s_book_is_a_404(
        self, db: dict[str, str], firm: dict[str, object], super_engine: Engine
    ) -> None:
        """A client on no adviser's book is invisible to a book-scoped adviser and visible to one
        holding contacts:read_all. Addressing them by uid must land the same way round: the
        resolve is a SELECT inside the caller's own RLS session, so the row is not there to find
        rather than found-and-then-refused. This is why the contact/account resolvers are NOT
        cached -- a process-wide map would answer from another caller's read."""
        unheld = f"crmcon_unheld_{uuid.uuid4().hex[:8]}"
        with super_engine.connect() as c:
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                    "VALUES (:t, :uid, 'individual', 'Unheld', 'advised')"
                ),
                {"t": int(firm["tenant"]), "uid": unheld},
            )

        async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
            assert await addressing.book_contact_id(contact_uid=unheld, session=session) > 0

        async with _authed(db["app"], str(firm["scoped_wos"]), []) as session:
            with pytest.raises(HTTPException) as raised:
                await addressing.book_contact_id(contact_uid=unheld, session=session)
        assert raised.value.status_code == 404

    def test_no_route_names_a_serial(self) -> None:
        """The contract, asserted on the router's own table rather than trusted to the reading
        order of a 2,000-line module: nothing in this router takes an id in its PATH."""
        paths = [getattr(r, "path", "") for r in crm_router.router.routes]
        offenders = [p for p in paths if "{contact_id}" in p or "{account_id}" in p]
        assert offenders == [], offenders


@pytest.mark.asyncio
async def test_scoped_adviser_reads_back_the_client_they_created(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """A book-scoped adviser (no contacts:read_all) creates a client and must be able to see it.

    crm.contacts RLS admits a row via contacts:read_all OR auth.contact_scope(), and
    contact_scope derives from crm.client_advisers -- so unless the create path writes the
    creating adviser's book row, the client vanishes the moment it is created.
    """
    async with _firm(db["app"]) as firm_session:
        created = await contacts_mod.create_contact(
            firm_session,
            tenant_id=int(firm["tenant"]),
            type="individual",
            surname="Caldwell",
            created_by_workos=str(firm["scoped_wos"]),
        )
    new_id = int(created["id"])

    async with _authed(db["app"], str(firm["scoped_wos"]), []) as session:
        rows = await crm_router.list_contacts(session=session)
        detail = await crm_router.get_contact(contact_id=new_id, session=session)
    assert new_id in {int(r["id"]) for r in rows}
    assert int(detail["contact"]["id"]) == new_id  # type: ignore[index]
    assert str(firm["scoped_id"]) in {str(a["adviser_id"]) for a in detail["advisers"]}  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_creating_a_client_requires_contacts_create_not_write(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """``contacts:write`` used to mint a client. It no longer does — editing a person already on
    the book is a different act from putting one there, and the implication never crosses from
    write to a sibling extra."""
    from fastapi import HTTPException

    from app.auth import Principal
    from app.db import FirmContext
    from app.routers import firm as firm_router

    async with _firm(db["app"]) as session:
        ctx = FirmContext(
            session=session,
            tenant_id=int(firm["tenant"]),
            principal=Principal(user_id=str(firm["scoped_wos"]), permissions=["contacts:write"]),
        )
        with pytest.raises(HTTPException) as exc:
            await firm_router.create_contact(
                body=firm_router.CreateContact(type="individual", surname="Rowntree"), ctx=ctx
            )
    assert exc.value.status_code == 403
    assert "contacts:create" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_the_create_contact_route_puts_the_creator_on_the_book(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """The same guarantee, end to end through the endpoint an adviser actually hits.

    The service layer only writes the book row when it is TOLD who the creator is, so the route
    passing ``created_by_workos=ctx.principal.user_id`` is the whole fix: with the route spreading
    the request body alone, ``POST /firm/contacts`` still lost the client. Asserting the book row
    isn't enough — this reads the client back on the creating adviser's own RLS session, which is
    what "the client vanished" actually meant."""
    from app.auth import Principal
    from app.db import FirmContext
    from app.routers import firm as firm_router

    async with _firm(db["app"]) as session:
        ctx = FirmContext(
            session=session,
            tenant_id=int(firm["tenant"]),
            principal=Principal(user_id=str(firm["scoped_wos"]), permissions=["contacts:create"]),
        )
        created = await firm_router.create_contact(
            body=firm_router.CreateContact(type="individual", surname="Rowntree"), ctx=ctx
        )
    new_id = int(created["id"])

    # No contacts:read_all — this adviser sees their book and nothing else.
    async with _authed(db["app"], str(firm["scoped_wos"]), []) as session:
        rows = await crm_router.list_contacts(session=session)
        detail = await crm_router.get_contact(contact_id=new_id, session=session)
    assert new_id in {int(r["id"]) for r in rows}
    assert int(detail["contact"]["id"]) == new_id  # type: ignore[index]
    assert [str(a["adviser_id"]) for a in detail["advisers"]] == [str(firm["scoped_id"])]  # type: ignore[union-attr]
    # scoped_id ("Adele Reviewer") was never marked `is_adviser` (0245: only an is_adviser login
    # may hold the 'adviser' role), so the creator lands on the book as 'support'.
    assert str(detail["advisers"][0]["role"]) == "support"  # type: ignore[index]


@pytest.mark.asyncio
async def test_an_operations_caller_creating_on_a_firms_behalf_is_not_put_on_its_book(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """The book row is written from a WorkOS id matched INSIDE the tenant, so a caller who isn't one
    of the firm's own people (Graphene ops provisioning for a firm) creates the client without
    quietly becoming its adviser — and without the create failing."""
    from app.auth import Principal
    from app.db import FirmContext
    from app.routers import firm as firm_router

    async with _firm(db["app"]) as session:
        ctx = FirmContext(
            session=session,
            tenant_id=int(firm["tenant"]),
            principal=Principal(user_id="wos_outsider_not_in_this_firm", permissions=["system"]),
        )
        created = await firm_router.create_contact(
            body=firm_router.CreateContact(type="organisation", name="Rowntree Trustees Ltd"),
            ctx=ctx,
        )
        booked = (
            await session.execute(
                text("SELECT count(*) FROM crm.client_advisers WHERE contact_id = :c"),
                {"c": int(created["id"])},
            )
        ).scalar_one()
    assert booked == 0


@pytest.mark.asyncio
async def test_a_client_with_a_balance_cannot_be_closed(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """Close is only for a client with nothing left open: no accounts, or every account worth
    zero. A valued book is not closeable from here — the live list would drop money that is
    still on it."""
    from fastapi import HTTPException

    from app.auth import Principal
    from app.db import FirmContext
    from app.routers import firm as firm_router

    booked = int(firm["booked"])

    def ctx(session: AsyncSession) -> FirmContext:
        return FirmContext(
            session=session,
            tenant_id=int(firm["tenant"]),
            principal=Principal(user_id=str(firm["scoped_wos"]), permissions=["contacts:write"]),
        )

    async with _firm(db["app"]) as session:
        with pytest.raises(HTTPException) as exc:
            await firm_router.archive_contact(contact_id=booked, ctx=ctx(session))
    assert exc.value.status_code == 409
    assert "balance" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_a_client_can_be_closed_and_reopened(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """Close takes them out of the live list; the record itself stays loadable so the header
    can say Closed, and Reopen brings them back. A close with no way to see or reverse it is
    a one-way door."""
    from app.auth import Principal
    from app.db import FirmContext
    from app.routers import firm as firm_router

    tenant = int(firm["tenant"])
    # A client with no accounts — Close's own rule. The fixture's two clients both hold money.
    async with _firm(db["app"]) as session:
        empty = int(
            (
                await session.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                        "VALUES (:t, :uid, 'individual', 'Closedwell', 'advised') RETURNING id"
                    ),
                    {"t": tenant, "uid": f"crmcon_empty_{firm['booked']}"},
                )
            ).scalar_one()
        )

    def ctx(session: AsyncSession) -> FirmContext:
        return FirmContext(
            session=session,
            tenant_id=tenant,
            principal=Principal(user_id=str(firm["all_wos"]), permissions=["contacts:write"]),
        )

    async with _firm(db["app"]) as session:
        closed = await firm_router.archive_contact(contact_id=empty, ctx=ctx(session))
    assert closed["deleted_at"] is not None

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        live = await crm_router.list_contacts(session=session)
        in_closed = await crm_router.list_contacts(archived=True, session=session)
        still_there = await crm_router.get_contact(contact_id=empty, session=session)
    assert empty not in {int(r["id"]) for r in live}
    assert empty in {int(r["id"]) for r in in_closed}
    assert int(still_there["contact"]["id"]) == empty  # type: ignore[index]
    assert still_there["contact"]["deleted_at"] is not None  # type: ignore[index]

    # Closing twice is not an error, and the row is not closed again.
    async with _firm(db["app"]) as session:
        assert (await firm_router.archive_contact(contact_id=empty, ctx=ctx(session)))[
            "already_archived"
        ] is True

    async with _firm(db["app"]) as session:
        await firm_router.restore_contact(contact_id=empty, ctx=ctx(session))
    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        live = await crm_router.list_contacts(session=session)
        restored = await crm_router.get_contact(contact_id=empty, session=session)
    assert empty in {int(r["id"]) for r in live}
    assert restored["contact"]["deleted_at"] is None  # type: ignore[index]


@pytest.mark.asyncio
async def test_a_client_with_money_in_flight_cannot_be_archived(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """Nothing settles an instruction, so closing a client with one pending would strand a live
    claim behind a row the live lists no longer show. Refused (409) until it is cancelled.
    The accounts are zeroed first so the refusal is the pending instruction, not the balance
    gate — Close is not offered on a valued client at all."""
    from fastapi import HTTPException

    from app import instructions as instructions_mod
    from app.auth import Principal
    from app.db import FirmContext
    from app.routers import firm as firm_router

    rich = int(firm["rich"])
    tenant = int(firm["tenant"])
    with super_engine.connect() as c:
        c.execute(
            text("UPDATE platform.accounts SET value = 0 WHERE contact_id = :c AND tenant_id = :t"),
            {"c": rich, "t": tenant},
        )
        c.commit()

    async with _firm(db["app"]) as session:
        raised = await instructions_mod.create_instruction(
            session,
            tenant_id=tenant,
            created_by_workos=None,
            kind="expected_in",
            account_id=int(firm["active_account"]),
            amount=5_000.0,
        )

    def ctx(session: AsyncSession) -> FirmContext:
        return FirmContext(
            session=session,
            tenant_id=tenant,
            principal=Principal(user_id=str(firm["all_wos"]), permissions=["contacts:write"]),
        )

    async with _firm(db["app"]) as session:
        with pytest.raises(HTTPException) as exc:
            await firm_router.archive_contact(contact_id=rich, ctx=ctx(session))
    assert exc.value.status_code == 409
    assert "pending instruction" in str(exc.value.detail)

    # Cancel it and the close goes through — the block is a state, not a permanent bar.
    async with _firm(db["app"]) as session:
        await instructions_mod.cancel_instruction(session, tenant_id=tenant, uid=str(raised["uid"]))
    async with _firm(db["app"]) as session:
        assert (await firm_router.archive_contact(contact_id=rich, ctx=ctx(session)))[
            "deleted_at"
        ] is not None


@pytest.mark.asyncio
async def test_value_as_of_survives_an_unrelated_write(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The as-at is when the value was SOURCED (`data.valued_at`, stamped by the ingest), not when
    the row was last touched: assigning a model or renaming an account bumps `updated_at` and
    would otherwise make a months-old snapshot read as today's valuation."""
    tenant = int(firm["tenant"])
    with super_engine.connect() as c:
        c.execute(
            text(
                "UPDATE platform.accounts SET data = jsonb_build_object("
                "  'value_source', 'seccl', 'valued_at', '2026-01-15T00:00:00+00:00') "
                "WHERE tenant_id = :t AND status = 'active'"
            ),
            {"t": tenant},
        )
        # ...then an unrelated write, which the updated_at trigger stamps with now().
        c.execute(
            text("UPDATE platform.accounts SET risk_level = 'balanced' WHERE tenant_id = :t"),
            {"t": tenant},
        )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.overview(session=session)
        rows = await crm_router.list_accounts(session=session)
    assert str(out["values_as_of"]).startswith("2026-01-15")
    valued = [r for r in rows if r["status"] == "active"]
    assert valued and all(str(r["value_as_of"]).startswith("2026-01-15") for r in valued)


@pytest.mark.asyncio
async def test_value_as_of_is_the_latest_valuation_for_a_pipeline_fed_account(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The provider pipeline stamps no `data.valued_at`; it writes `platform.valuations` and sets
    `accounts.value` to the latest row's `market_value`. That row's `as_of_date` is therefore the
    value's provenance. Before this the stamp fell straight through to `updated_at`, so a book
    whose holdings were dated a year earlier read "as at" the day somebody last touched the row."""
    tenant, active = int(firm["tenant"]), int(firm["active_account"])
    with super_engine.connect() as c:
        for day, mv in (("2026-03-01", 99000), ("2026-03-08", 100000)):
            c.execute(
                text(
                    "INSERT INTO platform.valuations "
                    "  (tenant_id, account_id, as_of_date, source_system, market_value) "
                    "VALUES (:t, :a, CAST(:d AS date), 'seccl', :mv)"
                ),
                {"t": tenant, "a": active, "d": day, "mv": mv},
            )
        # An unrelated write afterwards must not move the stamp.
        c.execute(
            text("UPDATE platform.accounts SET risk_level = 'balanced' WHERE id = :a"),
            {"a": active},
        )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_accounts(session=session)
        record = await crm_router.get_account(account_id=active, session=session)
    assert isinstance(rows, list)
    listed = next(r for r in rows if int(str(r["id"])) == active)
    assert str(listed["value_as_of"]).startswith("2026-03-08")
    assert str(record["value_as_of"]).startswith("2026-03-08")


@pytest.mark.asyncio
async def test_contact_detail_surfaces_kyc_relationships_and_note_total(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """KYC checks and related parties are stored by other subsystems and were never read; the
    notes list is bounded but reports the true total."""
    rich, booked, tenant = int(firm["rich"]), int(firm["booked"]), int(firm["tenant"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO crm.kyc_checks (uid, tenant_id, contact_id, kind, status, provider, "
                "  verified_at, valid_until) "
                "VALUES (:u, :t, :con, 'identity', 'passed', 'complycube', "
                "  now(), now() + interval '1 year')"
            ),
            {"u": f"kyc_{s}", "t": tenant, "con": rich},
        )
        c.execute(
            text(
                "INSERT INTO crm.relationships "
                "(tenant_id, from_contact_id, to_contact_id, relationship_type) "
                "VALUES (:t, :a, :b, 'spouse')"
            ),
            {"t": tenant, "a": rich, "b": booked},
        )
        for i in range(3):
            c.execute(
                text(
                    "INSERT INTO crm.contact_notes (tenant_id, contact_id, body) "
                    "VALUES (:t, :con, :b)"
                ),
                {"t": tenant, "con": rich, "b": f"note {i}"},
            )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        detail = await crm_router.get_contact(contact_id=rich, session=session)
    assert [k["kind"] for k in detail["kyc"]] == ["identity"]  # type: ignore[union-attr]
    assert detail["kyc"][0]["status"] == "passed"  # type: ignore[index]
    rel = detail["relationships"][0]  # type: ignore[index]
    assert rel["relationship_type"] == "spouse"
    assert rel["other_contact_id"] == booked and rel["outbound"] is True
    assert detail["notes_total"] == 3
    assert detail["accounts"][0]["value_as_of"] is not None  # type: ignore[index]
    assert {"cash", "growth"} <= detail["accounts"][0].keys()  # type: ignore[index]
    assert detail["value_source"] == "seccl"


@pytest.mark.asyncio
async def test_contact_detail_states_standing_movements_as_lists(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The client record's standing panel iterates these. Onboarding stored an object, and an
    object here stopped the whole page in the browser."""
    provider_row = {"amount": 100, "payment_day": 14, "auto_disinvest": "Largest"}
    with super_engine.connect() as c:
        c.execute(
            text(
                "UPDATE platform.accounts SET payments = CAST(:p AS jsonb), "
                "  withdrawals = CAST(:w AS jsonb) WHERE id = :a"
            ),
            {
                "p": json.dumps(
                    {
                        "initial_cash_deposit": {"investment_amount": 10_000},
                        "regular_payments": {"amount": 250, "frequency": "monthly"},
                    }
                ),
                "w": json.dumps([provider_row]),
                "a": int(firm["active_account"]),
            },
        )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        detail = await crm_router.get_contact(contact_id=int(firm["rich"]), session=session)
    account = next(
        a
        for a in detail["accounts"]  # type: ignore[union-attr]
        if int(a["id"]) == int(firm["active_account"])
    )
    assert [row["amount"] for row in account["payments"]] == [250.0]
    assert account["withdrawals"] == [provider_row]


@pytest.mark.asyncio
async def test_contact_detail_names_the_signed_w9_or_w8(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """`fatca_document` is the file behind the W-9 / W-8 answer (0261): its uid and name, or
    null when there is none. An archived file has left the client's documents, so it is null."""
    doc_uid = uuid.uuid4().hex[:12]
    with super_engine.connect() as c:
        doc_id = c.execute(
            text(
                "INSERT INTO documents.documents "
                "  (uid, tenant_id, document_type, source, name, status) "
                "VALUES (:u, :t, 'fatca_form', 'uploaded', 'W-9', 'generated') RETURNING id"
            ),
            {"u": doc_uid, "t": int(firm["tenant"])},
        ).scalar_one()
        c.execute(
            text(
                "UPDATE crm.contacts SET us_person = true, fatca_status = 'w9', "
                "  fatca_document_id = :d WHERE id = :c"
            ),
            {"d": doc_id, "c": int(firm["rich"])},
        )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        rich = await crm_router.get_contact(contact_id=int(firm["rich"]), session=session)
        booked = await crm_router.get_contact(contact_id=int(firm["booked"]), session=session)
    assert rich["contact"]["fatca_document"] == {"uid": doc_uid, "name": "W-9"}  # type: ignore[index]
    assert booked["contact"]["fatca_document"] is None  # type: ignore[index]

    with super_engine.connect() as c:
        c.execute(
            text("UPDATE documents.documents SET status = 'archived' WHERE id = :d"),
            {"d": doc_id},
        )
        c.commit()
    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        archived = await crm_router.get_contact(contact_id=int(firm["rich"]), session=session)
    assert archived["contact"]["fatca_document"] is None  # type: ignore[index]


@pytest.mark.asyncio
async def test_contact_shell_is_lean_chrome(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The record layout loads the shell, not the fat person. Notes, relationships, bank,
    FATCA and lake metrics stay off this payload so Documents/Chat/Transactions do not wait
    on them."""
    rich, tenant = int(firm["rich"]), int(firm["tenant"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO crm.kyc_checks (uid, tenant_id, contact_id, kind, status, provider) "
                "VALUES (:u, :t, :con, 'identity', 'passed', 'complycube')"
            ),
            {"u": f"kyc_shell_{s}", "t": tenant, "con": rich},
        )
        c.execute(
            text(
                "INSERT INTO crm.contact_notes (tenant_id, contact_id, body) "
                "VALUES (:t, :con, 'private')"
            ),
            {"t": tenant, "con": rich},
        )
        # The illustration composer reads its client off the shell: the three facts it asks for
        # when missing must be ON it, or it asks every client for all three.
        c.execute(
            text(
                "UPDATE crm.contacts SET dob = '1970-05-04', retirement_age = 67, "
                "marital_status = 'married' WHERE id = :con"
            ),
            {"con": rich},
        )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        shell = await crm_router.get_contact_shell(contact_id=rich, session=session)

    assert shell["contact"]["uid"]
    assert shell["contact"]["dob"] == "1970-05-04"  # type: ignore[index]
    assert shell["contact"]["retirement_age"] == 67  # type: ignore[index]
    assert shell["contact"]["marital_status"] == "married"  # type: ignore[index]
    assert shell["notes"] == []
    assert shell["notes_total"] == 0
    assert shell["relationships"] == []
    assert "bank_account" not in shell["contact"]
    assert "nationality" not in shell["contact"]
    assert "chats_unread" in shell
    assert [k["kind"] for k in shell["kyc"]] == ["identity"]  # type: ignore[union-attr]
    assert shell["accounts"]
    assert "cash" not in shell["accounts"][0]  # type: ignore[index]
    assert "value" in shell["accounts"][0]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_holdings_summary_is_the_donut_envelope(
    db: dict[str, str],
    firm: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overview asks for class totals, not every position. The portal's applyHoldingsSummary
    reads allocations/cash/book_cost/unrealised_gain/accounts — a tagged DuckDB list would
    paint an empty donut."""
    from app.lake import holdings as lake_holdings

    async def fake_summary(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        ext = ids[0]
        return [
            {
                "account_external_id": ext,
                "klass": "Cash",
                "value": 250.0,
                "book_cost": 250.0,
                "growth": 0.0,
            },
            {
                "account_external_id": ext,
                "klass": "Equities",
                "value": 750.0,
                "book_cost": 600.0,
                "growth": 150.0,
            },
        ]

    monkeypatch.setattr(lake_holdings, "firm_holdings_summary", fake_summary)

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.holdings_summary(contact_id=int(firm["rich"]), session=session)

    assert {row["label"] for row in out["allocations"]} == {"Cash", "Equities"}  # type: ignore[union-attr]
    assert out["cash"] == pytest.approx(250.0)
    assert out["book_cost"] == pytest.approx(850.0)
    assert out["unrealised_gain"] == pytest.approx(150.0)
    assert out["lake_total"] == pytest.approx(1000.0)
    assert out["accounts"]
    assert out["accounts"][0]["cash"] == pytest.approx(250.0)  # type: ignore[index]


@pytest.mark.asyncio
async def test_notes_are_capped_at_fifty_and_report_the_true_total(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """Every note used to be loaded with the record, unbounded. The list is now capped, so the tab
    has to be able to say what it is NOT showing -- which needs the true count, not len(notes).
    (55 notes: a 3-note fixture can never exercise the bound.)"""
    rich, tenant = int(firm["rich"]), int(firm["tenant"])
    with super_engine.connect() as c:
        for i in range(55):
            c.execute(
                text(
                    "INSERT INTO crm.contact_notes (tenant_id, contact_id, body, created_at) "
                    "VALUES (:t, :con, :b, now() - make_interval(mins => :i))"
                ),
                {"t": tenant, "con": rich, "b": f"note {i}", "i": i},
            )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        detail = await crm_router.get_contact(contact_id=rich, session=session)
    assert len(detail["notes"]) == 50  # type: ignore[arg-type]
    assert detail["notes_total"] == 55
    # Newest first, so the 50 shown are the 50 most recent -- not an arbitrary 50.
    assert detail["notes"][0]["body"] == "note 0"  # type: ignore[index]


@pytest.mark.asyncio
async def test_holdings_stand_on_the_same_accounts_as_aum(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed or draft account's positions were fed into the record's "Cash (provider)" figure and
    the allocation donut while being excluded from the AUM shown directly above them -- two money
    figures on one band, on two different account bases. Holdings now use the AUM basis.

    Transactions deliberately still include them: a closed account's movements are real history.
    They no longer go through the lake at all -- `/crm/transactions` reads `platform.transactions`
    (every provider, with the account/client/adviser joins a listing needs) -- so what is asserted
    here is that the OLTP read is scoped by the same RLS session and still carries the
    non-active accounts."""
    from app.lake import holdings as lake_holdings

    rich, tenant = int(firm["rich"]), int(firm["tenant"])
    with super_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, status::text FROM platform.accounts "
                "WHERE contact_id = :con AND status <> 'active'"
            ),
            {"con": rich},
        ).all()
        for aid, st in rows:
            c.execute(
                text(
                    "INSERT INTO platform.links "
                    "  (tenant_id, entity_type, entity_id, provider, external_id) "
                    "VALUES (:t, 'account', :eid, 'seccl', :ext)"
                ),
                {"t": tenant, "eid": aid, "ext": f"SECCL_{st}_{aid}"},
            )
        c.commit()
    assert {st for _, st in rows} == {"draft", "closed"}

    asked: dict[str, list[str]] = {}

    async def fake_holdings(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        asked["holdings"] = sorted(ids)
        return []

    monkeypatch.setattr(lake_holdings, "firm_holdings", fake_holdings)

    # One transaction on each of the client's accounts, including the draft and closed ones.
    with super_engine.connect() as c:
        for aid, st in [*rows, (int(firm["active_account"]), "active")]:
            c.execute(
                text(
                    "INSERT INTO platform.transactions "
                    "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                    "   amount, currency, transaction_date) "
                    "VALUES (:t, :a, 'seccl', :sid, 'deposit', 100, 'GBP', CURRENT_DATE)"
                ),
                {"t": tenant, "a": aid, "sid": f"TXN_{st}_{aid}"},
            )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        await crm_router.list_holdings(contact_id=rich, session=session)
        txns = await crm_router.list_transactions(contact_id=rich, session=session)

    assert asked["holdings"] == [
        str(firm["active_ext"])
    ]  # the draft/closed ids never reach the lake
    # Transactions keep all three: a closed account's movements are history, not noise.
    assert isinstance(txns, list)
    assert len(txns) == 3

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        through_today = await crm_router.list_transactions(
            contact_id=rich, date_to=date.today(), session=session
        )
        after_today = await crm_router.list_transactions(
            contact_id=rich, date_from=date.today() + timedelta(days=1), session=session
        )

    assert isinstance(through_today, list)
    assert len(through_today) == 3
    assert after_today == []


@pytest.mark.asyncio
async def test_the_client_home_stands_on_the_same_accounts_as_its_holdings_page(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/crm/home` built its own account query with no status predicate while `/crm/holdings`
    filtered on `_VALUED`, so a client with a closed linked account saw one total on their home and
    a different one on their holdings page — and the closed account still had a card. Home now uses
    the same basis; only activity keeps every account: a closed account's movements are history."""
    from contextlib import asynccontextmanager

    from app.lake import holdings as lake_holdings

    rich, tenant = int(firm["rich"]), int(firm["tenant"])
    with super_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, status::text FROM platform.accounts "
                "WHERE contact_id = :con AND status <> 'active'"
            ),
            {"con": rich},
        ).all()
        for aid, st in rows:
            c.execute(
                text(
                    "INSERT INTO platform.links "
                    "  (tenant_id, entity_type, entity_id, provider, external_id) "
                    "VALUES (:t, 'account', :eid, 'seccl', :ext)"
                ),
                {"t": tenant, "eid": aid, "ext": f"HOME_{st}_{aid}"},
            )
        # The onboarding hand-off activates the OLTP account before custodian operations creates
        # its platform.links row. It must still be a card in the client's portal; only lake-backed
        # holdings/value reads wait for the provider identity.
        c.execute(
            text(
                "INSERT INTO platform.accounts "
                "  (tenant_id, contact_id, product_id, custodian, status, uid, name, value, data) "
                "SELECT tenant_id, contact_id, product_id, custodian, 'active', "
                "       uid || '_local', 'Awaiting custodian', 0, "
                "       jsonb_build_object('custodian_open', jsonb_build_object("
                "         'local_activated', true, 'status', 'pending_ops')) "
                "FROM platform.accounts WHERE id = :a"
            ),
            {"a": int(firm["active_account"])},
        )
        c.commit()

    asked: dict[str, list[str]] = {}

    async def fake_holdings(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        asked["holdings"] = sorted(ids)
        return [
            {
                "account_external_id": i,
                "asset_name": "Vanguard FTSE Global All Cap",
                "value": 1000.0,
                "quantity": 1.0,
                "price": 1000.0,
                "book_cost": 900.0,
                "growth": 100.0,
            }
            for i in ids
        ]

    async def fake_txns(
        provider: str, uid: str, ids: list[str], limit: int = 100
    ) -> list[dict[str, object]]:
        asked["txns"] = sorted(ids)
        return []

    async def fake_series(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        asked["series"] = sorted(ids)
        return []

    monkeypatch.setattr(lake_holdings, "firm_holdings", fake_holdings)
    monkeypatch.setattr(lake_holdings, "firm_transactions", fake_txns)
    monkeypatch.setattr(lake_holdings, "firm_value_series", fake_series)

    # The "your adviser" card resolves through a service_role lookup of its own (a client can't read
    # crm.client_advisers under RLS); point it at the test container.
    @asynccontextmanager
    async def _sys() -> AsyncIterator[AsyncSession]:
        engine = create_async_engine(db["app"])
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            yield session
        await engine.dispose()

    monkeypatch.setattr(crm_router, "system_transaction", _sys)

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.home(contact_id=rich, session=session)

    assert asked["holdings"] == [str(firm["active_ext"])]  # not the draft/closed ones
    assert asked["series"] == [str(firm["active_ext"])]
    assert len(asked["txns"]) == 3  # ...but their movements are still history
    # Both active accounts are real, and both carry their OLTP `value` — the provider's stated
    # balance, the same figure the account record and the accounts listing show. The lake's
    # holdings rows (1000.0 here) supply what sits inside the value, never the value itself: this
    # page used to prefer their sum, and a client saw one number on the home card and another on
    # the account page it linked to. The locally activated account has no provider link yet and
    # is not sent to the lake at all.
    assert [(w["name"], w["value"]) for w in out["wrappers"]] == [  # type: ignore[union-attr]
        (str(firm["active_account_uid"]), 100000.0),
        ("Awaiting custodian", 0.0),
    ]
    assert out["totals"]["value"] == 100000.0  # type: ignore[index]


@pytest.mark.asyncio
async def test_home_shows_a_seeded_book_tagged_as_a_scheme_administrator(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graphene enables Morgan Lloyd first. Demo seed used to stamp every account link with that
    administrator code; `/crm/home` then dropped the wrappers because it only admitted custodian
    links — staff still saw the book, a client login saw none."""
    _fake_lake_empty(monkeypatch)
    _point_system_transaction_at(monkeypatch, db["app"])
    with super_engine.connect() as c:
        c.execute(
            text("UPDATE platform.accounts SET custodian = 'morgan-lloyd' WHERE id = :a"),
            {"a": int(firm["active_account"])},
        )
        c.execute(
            text(
                "UPDATE platform.links "
                "SET provider = 'morgan-lloyd', "
                "    data = COALESCE(data, '{}'::jsonb) || jsonb_build_object('seeded_demo', true) "
                "WHERE entity_type = 'account' AND entity_id = :a"
            ),
            {"a": int(firm["active_account"])},
        )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.home(contact_id=int(firm["rich"]), session=session)

    assert [w["name"] for w in out["wrappers"]] == [str(firm["active_account_uid"])]  # type: ignore[union-attr]
    assert out["totals"]["value"] == pytest.approx(100000.0)  # type: ignore[index]


@pytest.mark.asyncio
async def test_client_home_shows_the_wrapper_type_not_a_generic_account(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/crm/home` labels each card from `platform.products.product_type`. That table's read
    policy keyed only on `tenant_lineage()`, which is empty in the client portal, so the join
    produced NULL and every badge said "Account" — staff still saw ISA/SIPP."""
    _fake_lake_empty(monkeypatch)
    _point_system_transaction_at(monkeypatch, db["app"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        product_id = c.execute(
            text(
                "INSERT INTO platform.products (tenant_id, product_type, name, code, enabled) "
                "VALUES (:t, 'ISA', 'Stocks & Shares ISA', :code, true) RETURNING id"
            ),
            {"t": int(firm["tenant"]), "code": f"ISA_{s}"},
        ).scalar_one()
        c.execute(
            text("UPDATE platform.accounts SET product_id = :p WHERE id = :a"),
            {"p": product_id, "a": int(firm["active_account"])},
        )
        c.commit()
    wos = _mk_login(super_engine, int(firm["tenant"]), "wrap", ((int(firm["rich"]), "self"),))

    async with _authed(db["app"], wos, [], role="client") as session:
        out = await crm_router.home(session=session)

    assert [w["type"] for w in out["wrappers"]] == ["ISA"]  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Valuation provenance -- /crm/home and /crm/context stamp the book's as-of date so no
# figure in the client portal is shown without one (the UI renders `as at {date}`).
# ---------------------------------------------------------------------------


def _stamp_valued_at(super_engine: Engine, account_id: int, ts: str) -> None:
    """Stamp ``data.valued_at`` on an account -- the ingest's own provenance mark, which
    ``_VALUED_AT`` prefers over the row's ``updated_at``."""
    with super_engine.connect() as c:
        c.execute(
            text(
                "UPDATE platform.accounts "
                "SET data = COALESCE(data, '{}'::jsonb) "
                "  || jsonb_build_object('valued_at', CAST(:ts AS text)) "
                "WHERE id = :id"
            ),
            {"ts": ts, "id": account_id},
        )
        c.commit()


def _fake_lake_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No lake presence: home's figures then stand on the OLTP account values alone."""
    from app.lake import holdings as lake_holdings

    async def _empty(
        provider: str, uid: str, ids: list[str], limit: int = 100
    ) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(lake_holdings, "firm_holdings", _empty)
    monkeypatch.setattr(lake_holdings, "firm_transactions", _empty)
    monkeypatch.setattr(lake_holdings, "firm_value_series", _empty)


def _link_remaining_accounts(super_engine: Engine, contact: int, tenant: int, tag: str) -> None:
    """Seccl-link the fixture's draft + closed accounts (the active one already is), so home's
    account query sees all three and a test can prove which of them may vouch for `as_of`."""
    with super_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT id, status::text FROM platform.accounts "
                "WHERE contact_id = :con AND status <> 'active'"
            ),
            {"con": contact},
        ).all()
        for aid, st in rows:
            c.execute(
                text(
                    "INSERT INTO platform.links "
                    "  (tenant_id, entity_type, entity_id, provider, external_id) "
                    "VALUES (:t, 'account', :eid, 'seccl', :ext)"
                ),
                {"t": tenant, "eid": aid, "ext": f"{tag}_{st}_{aid}"},
            )
        c.commit()


def _account_ids_by_status(super_engine: Engine, contact: int) -> dict[str, int]:
    with super_engine.connect() as c:
        rows = c.execute(
            text("SELECT status::text, id FROM platform.accounts WHERE contact_id = :con"),
            {"con": contact},
        ).all()
    return {st: int(aid) for st, aid in rows}


@pytest.mark.asyncio
async def test_home_as_of_is_the_max_stamp_of_the_valued_accounts_only(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`as_of` is the valuation date of the book home actually shows: the max `_VALUED_AT`
    across VALUED accounts. The draft and closed accounts carry deliberately FRESHER stamps --
    their value is excluded from every figure, so their date must not vouch for it. Each
    surviving card carries its own `value_as_of`; with no series, `performance_period` is
    None (there is no chart basis to state)."""
    rich, tenant = int(firm["rich"]), int(firm["tenant"])
    _link_remaining_accounts(super_engine, rich, tenant, "ASOF")
    by_status = _account_ids_by_status(super_engine, rich)
    _stamp_valued_at(super_engine, by_status["active"], "2026-03-05T09:30:00+00:00")
    _stamp_valued_at(super_engine, by_status["draft"], "2026-07-20T09:30:00+00:00")
    _stamp_valued_at(super_engine, by_status["closed"], "2026-07-21T09:30:00+00:00")
    _fake_lake_empty(monkeypatch)
    # The caller sits on rich's book, so the adviser card reaches its service_role lookup.
    _point_system_transaction_at(monkeypatch, db["app"])

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.home(session=session)

    assert out["as_of"] == "2026-03-05"  # not the draft's or closed one's fresher stamps
    assert out["performance_period"] is None
    # Only the valued account keeps a card, and it states its own provenance.
    assert [w["value_as_of"] for w in out["wrappers"]] == ["2026-03-05"]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_home_as_of_folds_in_the_series_and_states_the_charts_basis(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chart can stand on a lake point newer than any account's value stamp: `as_of` takes
    the max of the two sources, and `performance_period` states the series' own first/last
    dates. When the account stamp is the fresher one, IT wins -- while the chart's stated
    basis stays the series'."""
    from app.lake import holdings as lake_holdings

    rich = int(firm["rich"])
    by_status = _account_ids_by_status(super_engine, rich)
    _stamp_valued_at(super_engine, by_status["active"], "2026-03-05T09:30:00+00:00")
    _fake_lake_empty(monkeypatch)
    _point_system_transaction_at(monkeypatch, db["app"])

    async def fake_series(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        return [
            {"account_external_id": e, "date": f"2026-06-{d:02d}", "value": 100.0 + d}
            for e in ids
            for d in range(1, 31)
        ]

    monkeypatch.setattr(lake_holdings, "firm_value_series", fake_series)

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.home(session=session)
    assert out["as_of"] == "2026-06-30"  # the series' last point beats the March stamp
    assert out["performance_period"] == {"from": "2026-06-01", "to": "2026-06-30"}

    _stamp_valued_at(super_engine, by_status["active"], "2026-07-02T08:00:00+00:00")
    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.home(session=session)
    assert out["as_of"] == "2026-07-02"  # ...and a fresher account stamp beats the series
    assert out["performance_period"] == {"from": "2026-06-01", "to": "2026-06-30"}


@pytest.mark.asyncio
async def test_home_provenance_is_null_for_an_accountless_client(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unlinked client has no valuation to date: `as_of` and `performance_period` are null,
    never a fabricated stamp."""
    tenant = int(firm["tenant"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        org = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, service_type) "
                    "VALUES (:t, :uid, 'organisation', :n, 'execution_only') RETURNING id"
                ),
                {"t": tenant, "uid": f"asoforg_{s}", "n": f"Dateless Trustees {s}"},
            ).scalar_one()
        )
        c.commit()
    wos = _mk_login(super_engine, tenant, "asof", ((org, "self"),))
    _fake_lake_empty(monkeypatch)
    _point_system_transaction_at(monkeypatch, db["app"])

    async with _authed(db["app"], wos, [], role="client") as session:
        out = await crm_router.home(session=session)
        ctx = await crm_router.context(session=session)
    assert out["as_of"] is None
    assert out["performance_period"] is None
    assert out["wrappers"] == []
    assert ctx["as_of"] is None


@pytest.mark.asyncio
async def test_context_as_of_stands_on_the_valued_accounts_alone(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`context` derives `as_of` from OLTP `platform.accounts` alone (it must never read the
    lake): the max `_VALUED_AT` over the caller's VALUED accounts -- the draft and closed
    ones carry fresher stamps that must not win, exactly as on home."""
    tenant, rich = int(firm["tenant"]), int(firm["rich"])
    by_status = _account_ids_by_status(super_engine, rich)
    _stamp_valued_at(super_engine, by_status["active"], "2026-04-11T09:30:00+00:00")
    _stamp_valued_at(super_engine, by_status["draft"], "2026-07-20T09:30:00+00:00")
    _stamp_valued_at(super_engine, by_status["closed"], "2026-07-21T09:30:00+00:00")
    wos = _mk_login(super_engine, tenant, "ctxasof", ((rich, "self"),))
    _point_system_transaction_at(monkeypatch, db["app"])

    async with _authed(db["app"], wos, [], role="client") as session:
        out = await crm_router.context(session=session)
    assert out["as_of"] == "2026-04-11"


# ---------------------------------------------------------------------------
# /crm/context -- the client portal's "who am I" endpoint. Its response shape is FROZEN
# (the SvelteKit composed-page loader projects it by key), so the exact key set is pinned
# here alongside the RLS behaviour: a client gets their own party + their own counts, an
# unlinked staff member gets an anonymous book-wide view, and the adviser card can never
# diverge from /crm/home's (both stand on the shared _adviser_for_caller helper).
# ---------------------------------------------------------------------------

_CONTEXT_KEYS = {
    "contact_id",
    "type",
    "service_type",
    "account_count",
    "valued_account_count",
    "adviser",
    "as_of",
}


def _mk_login(
    super_engine: Engine,
    tenant: int,
    tag: str,
    links: tuple[tuple[int, str], ...] = (),
) -> str:
    """A login in the firm's tenant; ``links`` are (contact_id, capacity) crm.contact_users
    rows -- empty for an unlinked staff member. Returns the WorkOS user id for _authed."""
    s = uuid.uuid4().hex[:8]
    wos = f"ctxwos_{tag}_{s}"
    with super_engine.connect() as c:
        uid = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                    "  first_name, last_name, enabled) "
                    "VALUES (:uid, :t, :email, :wos, :first, :last, true) RETURNING id"
                ),
                {
                    "uid": f"ctxusr_{tag}_{s}",
                    "t": tenant,
                    "email": f"{tag}_{s}@ctxfirm.test",
                    "wos": wos,
                    "first": "Ctx",
                    "last": tag.capitalize(),
                },
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                "VALUES (:u, :t, :p)"
            ),
            {"u": uid, "t": tenant, "p": "client" if links else "platform"},
        )
        for contact, capacity in links:
            c.execute(
                text(
                    "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                    "VALUES (:t, :con, :u, :cap)"
                ),
                {"t": tenant, "con": contact, "u": uid, "cap": capacity},
            )
        c.commit()
    return wos


def _point_system_transaction_at(monkeypatch: pytest.MonkeyPatch, app_url: str) -> None:
    """The adviser card resolves through a service_role lookup of its own (a client can't read
    crm.client_advisers under RLS); point it at the test container."""

    @asynccontextmanager
    async def _sys() -> AsyncIterator[AsyncSession]:
        engine = create_async_engine(app_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            yield session
        await engine.dispose()

    monkeypatch.setattr(crm_router, "system_transaction", _sys)


@pytest.mark.asyncio
async def test_context_pins_its_exact_key_set(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The composed-page loader projects this response by key, so the shape is frozen: exactly
    these keys, no more, no fewer. The unlinked-staff branch exercises it too -- every key must
    be present even when the caller has no contact link."""
    wos = _mk_login(super_engine, int(firm["tenant"]), "keys")
    async with _authed(db["app"], wos, ["contacts:read_all"]) as session:
        out = await crm_router.context(session=session)
    assert set(out) == _CONTEXT_KEYS


@pytest.mark.asyncio
async def test_context_scopes_to_the_calling_client(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two clients of the same firm get different identities and different counts from the same
    query -- RLS scoping, not a WHERE clause. Rich holds three accounts of which one is valued
    (the draft and closed ones are excluded on the same _VALUED basis as every AUM figure);
    booked holds one."""
    tenant, rich, booked = int(firm["tenant"]), int(firm["rich"]), int(firm["booked"])
    rich_wos = _mk_login(super_engine, tenant, "rich", ((rich, "self"),))
    booked_wos = _mk_login(super_engine, tenant, "booked", ((booked, "self"),))
    _point_system_transaction_at(monkeypatch, db["app"])

    async with _authed(db["app"], rich_wos, [], role="client") as session:
        rich_out = await crm_router.context(session=session)
    async with _authed(db["app"], booked_wos, [], role="client") as session:
        booked_out = await crm_router.context(session=session)

    assert rich_out["contact_id"] == rich
    assert rich_out["type"] == "individual"
    assert rich_out["service_type"] == "advised"
    assert rich_out["account_count"] == 3  # active + draft + closed: all theirs, all visible
    assert rich_out["valued_account_count"] == 1  # ...but only the active one carries value

    assert booked_out["contact_id"] == booked
    assert booked_out["account_count"] == 1
    assert booked_out["valued_account_count"] == 1


@pytest.mark.asyncio
async def test_context_for_unlinked_staff_is_anonymous_and_book_wide(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """A staff principal with no contact link gets no identity (contact_id/type/service_type all
    None, adviser None -- the helper never reaches its service_role lookup on an empty scope) and
    counts over whatever RLS lets them see: with contacts:read_all, the whole book."""
    wos = _mk_login(super_engine, int(firm["tenant"]), "staff")
    async with _authed(db["app"], wos, ["contacts:read_all"]) as session:
        out = await crm_router.context(session=session)
    assert out["contact_id"] is None
    assert out["type"] is None
    assert out["service_type"] is None
    assert out["adviser"] is None
    assert out["account_count"] == 4
    assert out["valued_account_count"] == 2
    assert out["as_of"] is not None  # the book carries value, so it carries a date


@pytest.mark.asyncio
async def test_context_and_home_agree_on_the_adviser(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adviser lookup was extracted out of home() into _adviser_for_caller so context could
    share it; the refactor must not fork behaviour -- the same caller gets the identical card
    from both surfaces."""
    from app.lake import holdings as lake_holdings

    async def _empty(
        provider: str, uid: str, ids: list[str], limit: int = 100
    ) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(lake_holdings, "firm_holdings", _empty)
    monkeypatch.setattr(lake_holdings, "firm_transactions", _empty)
    monkeypatch.setattr(lake_holdings, "firm_value_series", _empty)
    _point_system_transaction_at(monkeypatch, db["app"])

    wos = _mk_login(super_engine, int(firm["tenant"]), "advparity", ((int(firm["rich"]), "self"),))
    async with _authed(db["app"], wos, [], role="client") as session:
        home_out = await crm_router.home(session=session)
        ctx_out = await crm_router.context(session=session)

    assert home_out["adviser"] is not None  # rich has two advisers on the book
    assert ctx_out["adviser"] == home_out["adviser"]
    assert set(ctx_out["adviser"]) == {"name", "firm", "email", "phone"}  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_context_round_trips_an_organisation_contact(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`type` keys the per-audience page layouts, so the organisation branch must survive the
    round trip -- an org client with no accounts and no adviser still gets a complete response."""
    tenant = int(firm["tenant"])
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        org = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, service_type) "
                    "VALUES (:t, :uid, 'organisation', :n, 'execution_only') RETURNING id"
                ),
                {"t": tenant, "uid": f"ctxorg_{s}", "n": f"Acme Trustees {s}"},
            ).scalar_one()
        )
        c.commit()
    wos = _mk_login(super_engine, tenant, "org", ((org, "self"),))
    _point_system_transaction_at(monkeypatch, db["app"])

    async with _authed(db["app"], wos, [], role="client") as session:
        out = await crm_router.context(session=session)
    assert out["contact_id"] == org
    assert out["type"] == "organisation"
    assert out["service_type"] == "execution_only"
    assert out["account_count"] == 0
    assert out["valued_account_count"] == 0
    assert out["adviser"] is None
    assert out["as_of"] is None  # no valued account, no date to claim


@pytest.mark.asyncio
async def test_context_prefers_the_callers_own_party_over_a_delegation(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A login can also act for someone ELSE's party (capacity 'authorised' / POA / delegate);
    their OWN contact ('self') must win even when the delegated link has the lower contact id --
    the audience layout keys off who they ARE, not who they can act for."""
    tenant, rich, booked = int(firm["tenant"]), int(firm["rich"]), int(firm["booked"])
    assert rich < booked  # the delegated link deliberately sorts first by id
    wos = _mk_login(super_engine, tenant, "poa", ((rich, "authorised"), (booked, "self")))
    _point_system_transaction_at(monkeypatch, db["app"])

    async with _authed(db["app"], wos, [], role="client") as session:
        out = await crm_router.context(session=session)
    assert out["contact_id"] == booked
    # ...while the counts still cover everything they can act for: rich's three + booked's one.
    assert out["account_count"] == 4
    assert out["valued_account_count"] == 2


# ---------------------------------------------------------------------------------------------
# Active-contact selector -- a login linked to more than one client (docs/portal-customisation.md)


def _mk_named_contact(super_engine: Engine, tenant: int, name: str) -> int:
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, service_type) "
                    "VALUES (:t, :uid, 'individual', :n, 'advised') RETURNING id"
                ),
                {"t": tenant, "uid": f"actcon_{s}", "n": name},
            ).scalar_one()
        )
        c.commit()
    return contact


@pytest.mark.asyncio
async def test_my_contacts_lists_alphabetically_but_defaults_to_the_login_owner(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """A login sees every scoped contact name-sorted, but opens as the person who signed in.

    A JISA parent's scope includes the child. Alphabetical defaulting could therefore open the
    child's slice and hide the parent's own terms and identity work immediately after login.
    """
    tenant = int(firm["tenant"])
    zed = _mk_named_contact(super_engine, tenant, "Zed Ordinary")
    amara = _mk_named_contact(super_engine, tenant, "Amara First")
    wos = _mk_login(super_engine, tenant, "multi", ((zed, "self"), (amara, "authorised")))

    async with _authed(db["app"], wos, [], role="client") as session:
        out = await crm_router.my_contacts(session=session)

    assert [c["id"] for c in out["contacts"]] == [amara, zed]
    assert out["contacts"][0]["name"] == "Amara First"
    assert out["contacts"][0]["is_self"] is False
    assert out["contacts"][1]["is_self"] is True
    assert out["default_contact_id"] == zed


@pytest.mark.asyncio
async def test_my_contacts_is_empty_for_a_single_contact_login(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The common case -- one linked contact -- still returns exactly one entry, which is what
    the portal's switcher uses to decide whether to render itself at all."""
    tenant, rich = int(firm["tenant"]), int(firm["rich"])
    wos = _mk_login(super_engine, tenant, "solo", ((rich, "self"),))

    async with _authed(db["app"], wos, [], role="client") as session:
        out = await crm_router.my_contacts(session=session)

    assert [c["id"] for c in out["contacts"]] == [rich]
    assert out["contacts"][0]["is_self"] is True
    assert out["default_contact_id"] == rich


@pytest.mark.asyncio
async def test_registered_contact_slice_includes_the_childs_account(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The portal passes the active adult's contact id on every account/home/holdings read.

    RLS already admits the JISA through 0156's `account_ids`; the explicit contact filter must not
    narrow it straight back out merely because the account correctly belongs to the child.
    """
    tenant = int(firm["tenant"])
    parent = _mk_named_contact(super_engine, tenant, "Louise Byford")
    wos = _mk_login(super_engine, tenant, "jisa_parent", ((parent, "self"),))
    with super_engine.begin() as conn:
        application = conn.execute(
            text(
                "INSERT INTO crm.applications (tenant_id, uid, status) "
                "VALUES (:t, :uid, 'in_progress') RETURNING id"
            ),
            {"t": tenant, "uid": f"app_jisa_{uuid.uuid4().hex[:8]}"},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO crm.account_contacts "
                "  (tenant_id, application_id, account_id, contact_id, role) "
                "VALUES (:t, :application, :account, :parent, 'registered_contact')"
            ),
            {
                "t": tenant,
                "application": application,
                "account": firm["active_account"],
                "parent": parent,
            },
        )

    async with _authed(db["app"], wos, [], role="client") as session:
        out = await crm_router.list_accounts(contact_id=parent, session=session)

    assert isinstance(out, list)
    assert [row["id"] for row in out] == [firm["active_account"]]


@pytest.mark.asyncio
async def test_context_honours_a_valid_active_contact_override(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With an explicit ?contact_id= naming a DIFFERENT contact than the one the fallback chain
    would pick, the override wins -- and the counts narrow to just that one contact's book,
    not the union of everything this login can act for."""
    tenant, rich, booked = int(firm["tenant"]), int(firm["rich"]), int(firm["booked"])
    wos = _mk_login(super_engine, tenant, "override", ((booked, "self"), (rich, "authorised")))
    _point_system_transaction_at(monkeypatch, db["app"])

    async with _authed(db["app"], wos, [], role="client") as session:
        default_out = await crm_router.context(session=session)
        overridden_out = await crm_router.context(session=session, contact_id=rich)

    assert default_out["contact_id"] == booked  # unchanged: self still wins with no override
    assert default_out["account_count"] == 4  # union of both, as before

    assert overridden_out["contact_id"] == rich
    assert overridden_out["account_count"] == 3  # rich's own three, not the union
    assert overridden_out["valued_account_count"] == 1


@pytest.mark.asyncio
async def test_context_ignores_an_out_of_scope_contact_override(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ?contact_id= naming a contact this login has no link to at all (e.g. a tampered cookie)
    is silently ignored, not honoured -- falls back to the normal resolution, never widens
    access to a party outside auth.contact_scope()."""
    tenant, rich, booked = int(firm["tenant"]), int(firm["rich"]), int(firm["booked"])
    wos = _mk_login(super_engine, tenant, "notmine", ((rich, "self"),))
    _point_system_transaction_at(monkeypatch, db["app"])

    async with _authed(db["app"], wos, [], role="client") as session:
        out = await crm_router.context(session=session, contact_id=booked)

    assert out["contact_id"] == rich  # NOT booked -- the override was refused
    assert out["account_count"] == 3  # rich's own book, not booked's


@pytest.mark.asyncio
async def test_home_narrows_accounts_to_the_active_contact(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/crm/home?contact_id= narrows the wrapper cards to just that one contact's accounts --
    the same active-contact selector as /crm/context, applied to the home surface."""
    from app.lake import holdings as lake_holdings

    async def _empty(
        provider: str, uid: str, ids: list[str], limit: int = 100
    ) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(lake_holdings, "firm_holdings", _empty)
    monkeypatch.setattr(lake_holdings, "firm_transactions", _empty)
    monkeypatch.setattr(lake_holdings, "firm_value_series", _empty)
    _point_system_transaction_at(monkeypatch, db["app"])

    tenant, rich, booked = int(firm["tenant"]), int(firm["rich"]), int(firm["booked"])
    # home() inner-joins platform.links (Seccl) -- the fixture's `booked` account has none, so
    # it would be invisible to home() regardless of contact_id filtering. Give it one.
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        booked_account = int(
            c.execute(
                text("SELECT id FROM platform.accounts WHERE contact_id = :c"), {"c": booked}
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, external_id) "
                "VALUES (:t, 'account', :eid, 'seccl', :ext)"
            ),
            {"t": tenant, "eid": booked_account, "ext": f"SECCL_BOOKED_{s}"},
        )
        c.commit()
    wos = _mk_login(super_engine, tenant, "homemulti", ((rich, "self"), (booked, "authorised")))

    async with _authed(db["app"], wos, [], role="client") as session:
        unfiltered = await crm_router.home(session=session)
        narrowed = await crm_router.home(session=session, contact_id=booked)

    assert len(unfiltered["wrappers"]) == 2  # rich's one valued account + booked's one
    assert len(narrowed["wrappers"]) == 1
    assert narrowed["totals"]["value"] == 25000.0  # booked's account alone, not the union


@pytest.mark.asyncio
async def test_list_accounts_narrows_to_the_active_contact(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """/crm/accounts?contact_id= narrows to that one client's accounts, same pattern as home."""
    tenant, rich, booked = int(firm["tenant"]), int(firm["rich"]), int(firm["booked"])
    wos = _mk_login(super_engine, tenant, "acctsmulti", ((rich, "self"), (booked, "authorised")))

    async with _authed(db["app"], wos, [], role="client") as session:
        unfiltered = await crm_router.list_accounts(session=session)
        narrowed = await crm_router.list_accounts(session=session, contact_id=booked)

    assert isinstance(unfiltered, list) and len(unfiltered) == 4  # rich's three + booked's one
    assert isinstance(narrowed, list) and len(narrowed) == 1
    assert narrowed[0]["contact_id"] == booked


# ---------------------------------------------------------------------------------------------
# Drawdown SSO gate (permission audit H1)


@pytest.mark.asyncio
async def test_drawdown_session_refuses_a_principal_without_instructions_write(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """Minting a DCube drawdown SSO URL is an instruction, not a read: a client principal (or a
    zero-permission staff login) whose RLS scope covers the account must be refused BEFORE any
    vendor interaction. This endpoint used to carry no gate at all; since the 0042 split the gate
    is `instructions:write` (money movement), not `contacts:write`."""
    from fastapi import HTTPException

    from app.auth import Principal

    async with _authed(db["app"], str(firm["all_wos"]), [], role="client") as session:
        with pytest.raises(HTTPException) as exc:
            await crm_router.account_drawdown_session(
                account_id=int(firm["active_account"]),
                principal=Principal(user_id=str(firm["all_wos"]), permissions=[]),
                session=session,
            )
    assert exc.value.status_code == 403
    assert "instructions:write" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_drawdown_session_proceeds_for_instructions_write_staff(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate must not lock out the people the surface is FOR: an adviser holding
    instructions:write on a Morgan-Lloyd-linked SIPP reaches the vendor call (mocked, per the
    existing Morgan Lloyd tests) and gets the SSO payload back. contacts:write alone no longer
    reaches it — the money-movement split (0042) is what this pins."""
    from app.auth import Principal

    account_id = await _pension_account(
        super_engine,
        firm,
        product="SIPP",
        link_contact=True,
        administer=True,
        link_scheme=True,
    )

    seen: dict[str, object] = {}

    async def _fake_drawdown(contact_guid: str, **kwargs: object) -> dict[str, object]:
        seen.update(contact=contact_guid, **kwargs)
        return {"sso_url": "https://dcube.example/session"}

    monkeypatch.setattr(crm_router.morgan_lloyd, "drawdown_session", _fake_drawdown)

    async with _authed(db["app"], str(firm["all_wos"]), ["instructions:write"]) as session:
        out = await crm_router.account_drawdown_session(
            account_id=account_id,
            principal=Principal(user_id=str(firm["all_wos"]), permissions=["instructions:write"]),
            session=session,
        )
    assert out == {"sso_url": "https://dcube.example/session"}
    assert str(seen["contact"]).startswith("ML_GUID_")
    assert str(seen["scheme_ref"]).startswith("MLSIPP/")


@pytest.mark.asyncio
async def test_the_account_detail_carries_the_owners_vulnerability(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The account record states the owner's vulnerability, so the endpoint has to return it.

    The account page is a place an adviser moves someone's money without necessarily having opened
    their record, and FG21/1 governs how that conversation is conducted -- so the flag travels with
    the account (0054). Both halves are asserted because they can disagree: the SELECT aliases the
    columns `c_*` and the handler nests them under `client`, and aliasing without nesting leaks
    `c_is_vulnerable` into the top level instead.
    """
    with super_engine.connect() as c:
        c.execute(
            text(
                "UPDATE crm.contacts SET is_vulnerable = true, "
                "  vulnerability_reasons = ARRAY['health', 'life_event']::text[], "
                "  vulnerability_is_temporary = true, "
                "  vulnerability_notes = 'Recently bereaved.', "
                "  vulnerability_recorded_at = now() "
                "WHERE id = :id"
            ),
            {"id": firm["rich"]},
        )
        fees = [
            int(
                c.execute(
                    text(
                        "INSERT INTO platform.fee_packages "
                        "  (tenant_id, name, category, frequency, fee_type, rule) "
                        "VALUES (:t, :n, 'adviser', 'ongoing', 'fixed', "
                        "        '{\"amount\": 10}'::jsonb) RETURNING id"
                    ),
                    {"t": firm["tenant"], "n": name},
                ).scalar_one()
            )
            for name in ("Advice charge", "Platform charge")
        ]
        c.execute(
            text(
                "INSERT INTO platform.account_fee_packages "
                "  (tenant_id, account_id, fee_package_id, sort_order) "
                "VALUES (:t, :a, :second, 0), (:t, :a, :first, 1)"
            ),
            {
                "t": firm["tenant"],
                "a": firm["active_account"],
                "first": fees[0],
                "second": fees[1],
            },
        )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.get_account(
            account_id=int(str(firm["active_account"])), session=session
        )

    client = out["client"]
    assert isinstance(client, dict)
    assert client["is_vulnerable"] is True
    assert client["vulnerability_reasons"] == ["health", "life_event"]
    assert client["vulnerability_is_temporary"] is True
    assert client["vulnerability_notes"] == "Recently bereaved."
    assert client["vulnerability_recorded_at"] is not None
    assert {
        "address",
        "bank_account",
        "relevant_uk_earnings",
        "pension_protection",
    } <= client.keys()
    assert {
        "product_id",
        "model_id",
        "fee_packages",
        "bank_details_allowed",
        "third_party_pension_provider",
        "bank_accounts",
    } <= out.keys()
    assert out["bank_accounts"] is None
    assert out["bank_details_allowed"] is True
    assert isinstance(out["fee_packages"], list)
    assert [(row["id"], row["name"], row["sort_order"]) for row in out["fee_packages"]] == [
        (fees[1], "Platform charge", 0),
        (fees[0], "Advice charge", 1),
    ]
    # THE CHARGE TRAVELS WITH THE ASSIGNMENT. This projected the id, the name and the order, so
    # the record's Fees card could name the firm's pricing label and state neither the figure nor
    # how often it is taken — the one question that card is opened to answer.
    assert out["fee_packages"][0]["fee_type"] == "fixed"
    assert out["fee_packages"][0]["rule"] == {"amount": 10}
    assert out["fee_packages"][0]["frequency"] == "ongoing"
    assert {
        "charge_basis",
        "payer",
        "currency",
        "vatable",
        "minimum_amount",
        "maximum_amount",
        "description",
    } <= out["fee_packages"][0].keys()
    # Nested, not leaked: nothing `c_`-prefixed survives on the account itself.
    assert not [key for key in out if key.startswith("c_")]


@pytest.mark.asyncio
async def test_the_account_detail_projects_its_own_bank_accounts(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The account card edits `platform.accounts.bank_accounts`, so GET has to return them.

    Until it did, the surface always read `client.bank_account` and the fallback sentence
    ("This account uses the client's bank details") stayed up after a save that had nowhere
    to land on the read. The nested client details stay — they are the fallback when this
    list is empty — and they are a different object so a reader can tell them apart.
    """
    with super_engine.connect() as c:
        c.execute(
            text("UPDATE platform.accounts SET bank_accounts = CAST(:b AS jsonb) WHERE id = :a"),
            {
                "a": int(firm["active_account"]),
                "b": json.dumps(
                    [
                        {
                            "name": "Oakden SIPP",
                            "sort_code": "11-11-11",
                            "account_number": "11111111",
                        }
                    ]
                ),
            },
        )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.get_account(
            account_id=int(str(firm["active_account"])), session=session
        )

    assert out["bank_accounts"] == [
        {"name": "Oakden SIPP", "sort_code": "11-11-11", "account_number": "11111111"}
    ]
    assert "bank_account" in out["client"]
    # Nested, not leaked: the array is on the account, not a `c_bank_accounts` leftover.
    assert "c_bank_accounts" not in out


@pytest.mark.asyncio
async def test_withdrawal_fees_are_configured_not_hardcoded(
    db: dict[str, str],
    super_engine: Engine,
    firm: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The account read exposes configured SEI fees from the same effective custodian as submit."""
    with super_engine.connect() as c:
        c.execute(
            text("UPDATE platform.accounts SET custodian = NULL WHERE id = :a"),
            {"a": int(str(firm["active_account"]))},
        )
        c.execute(
            text(
                "UPDATE platform.links SET provider = 'sei' "
                "WHERE tenant_id = :t AND entity_type = 'account' AND entity_id = :a "
                "  AND link_type = 'primary'"
            ),
            {"t": int(str(firm["tenant"])), "a": int(str(firm["active_account"]))},
        )
        c.commit()
    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.get_account(
            account_id=int(str(firm["active_account"])), session=session
        )
    assert out["withdrawal_fees"] == {
        "faster_payment": settings.sei_faster_payment_fee,
        "swift": settings.sei_swift_fee,
    }

    monkeypatch.setattr(settings, "sei_faster_payment_fee", 0.75)
    monkeypatch.setattr(settings, "sei_swift_fee", 20.0)
    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        reconfigured = await crm_router.get_account(
            account_id=int(str(firm["active_account"])), session=session
        )
    assert reconfigured["withdrawal_fees"] == {"faster_payment": 0.75, "swift": 20.0}

    with super_engine.connect() as c:
        c.execute(
            text("UPDATE platform.accounts SET custodian = 'seccl' WHERE id = :a"),
            {"a": int(str(firm["active_account"]))},
        )
        c.commit()
    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        other_custodian = await crm_router.get_account(
            account_id=int(str(firm["active_account"])), session=session
        )
    assert other_custodian["withdrawal_fees"] is None


# ---------------------------------------------------------------------------------------------
# Drawdown PRECONDITIONS — the two refusals the journey actually hits
#
# The gate tests above prove who may call it. These prove what the account must BE, which is where
# the journey keeps failing in practice. Neither refusal was asserted, and they are easy to
# misread: both say "drawdown does not work here" while meaning different things, and the fix for
# one (sync the client to Morgan Lloyd) does nothing for the other (the account is not a pension).
#
# `cam_setmemberdetails` addresses the member by Dynamics GUID and this account's scheme ref.
# OpenAPI marks `illustrationTemplate` optional and the mint succeeds without it; DCube loads
# that CTC1 XML after SSO. A 500 on the SSO URL itself is CTC `SSOLogin` (`docs/gotchas.md`).
# The contact record also matters earlier — `cam_createcontact` needs gender and the rest.


async def _pension_account(
    super_engine: Engine,
    firm: dict[str, object],
    *,
    product: str,
    link_contact: bool,
    administer: bool = False,
    link_scheme: bool = False,
) -> int:
    """An account of `product`, owned by the firm's client, optionally ML-linked.

    `administer` stamps the shelf mapping that `GET /crm/accounts` publishes as
    `scheme_administrator`. Drawdown itself does not read that column: `link_scheme` is THIS
    account's Morgan Lloyd scheme reference, distinct from the client's contact GUID, which is
    what used to be enough to mint a session against a pot the administrator has never heard of.
    """
    s = uuid.uuid4().hex[:8]
    tenant, rich = int(firm["tenant"]), int(firm["rich"])
    with super_engine.connect() as c:
        # The registry row still has to exist — `sync_platforms` seeds it on startup and the
        # merge resolves `platform.platforms.provider` — but the product no longer hangs off it
        # (0130 dropped `products.platform_id`), so the id is not bound to anything.
        c.execute(text(SECCL_PLATFORM_SQL), {"n": f"MLpre {s}"})
        product_id = int(
            c.execute(
                text(
                    "INSERT INTO platform.products (product_type, name) "
                    "VALUES (CAST(:pt AS platform.product_type), :n) RETURNING id"
                ),
                {"pt": product, "n": f"{product} {s}"},
            ).scalar_one()
        )
        account_id = int(
            c.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, contact_id, uid, status, value, product_id, custodian) "
                    "VALUES (:t, :con, :uid, 'active', '50000.00', :prod, 'sei') RETURNING id"
                ),
                {"t": tenant, "con": rich, "uid": f"pre_{s}", "prod": product_id},
            ).scalar_one()
        )
        if administer:
            c.execute(
                text(
                    "INSERT INTO platform.product_platforms "
                    "  (tenant_id, product_id, provider, role) "
                    "VALUES (:t, :p, 'morgan-lloyd', 'scheme_administrator')"
                ),
                {"t": tenant, "p": product_id},
            )
        if link_contact:
            c.execute(
                text(
                    "INSERT INTO platform.links "
                    "(tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                    "VALUES (:t, 'contact', :con, 'morgan-lloyd', 'primary', :ext)"
                ),
                {"t": tenant, "con": rich, "ext": f"ML_GUID_{s}"},
            )
        if link_scheme:
            c.execute(
                text(
                    "INSERT INTO platform.links "
                    "(tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                    "VALUES (:t, 'account', :a, 'morgan-lloyd', 'primary', :ext)"
                ),
                {"t": tenant, "a": account_id, "ext": f"MLSIPP/{s}"},
            )
        c.commit()
    return account_id


@pytest.mark.asyncio
async def test_drawdown_refuses_a_non_pension_account_before_the_vendor(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400, and no vendor call. This refusal was returned for EVERY account in the book —
    real SIPPs included — while `platform.platforms` had no rows, because `_sync_products` then
    rejected every product and no account carried a `product_id` at all. So the message named the
    wrong cause: nothing was wrong with the account, the shelf was empty."""
    from fastapi import HTTPException

    from app.auth import Principal

    account_id = await _pension_account(super_engine, firm, product="ISA", link_contact=True)
    called = False

    async def _never(*_a: object, **_k: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(crm_router.morgan_lloyd, "drawdown_session", _never)
    async with _authed(db["app"], str(firm["all_wos"]), ["instructions:write"]) as session:
        with pytest.raises(HTTPException) as exc:
            await crm_router.account_drawdown_session(
                account_id=account_id,
                principal=Principal(
                    user_id=str(firm["all_wos"]), permissions=["instructions:write"]
                ),
                session=session,
            )
    assert exc.value.status_code == 400
    assert "pension" in str(exc.value.detail).lower()
    assert not called, "a non-pension account must not reach Morgan Lloyd"


@pytest.mark.asyncio
async def test_drawdown_refuses_a_pension_whose_client_has_no_morgan_lloyd_link(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """409, and no vendor call — the state a real book sits in until the client is synced.

    `cam_setmemberdetails` addresses the member by their Dynamics GUID, which lives on
    `platform.links`, so a pension account whose owner has never been pushed to Morgan Lloyd cannot
    be drawn down however complete the contact record is. That is the distinction worth pinning: the
    contact's own fields are what `POST /crm/contacts/{id}/sync-to-morgan-lloyd` needs, and the link
    it writes is what THIS needs.
    """
    from fastapi import HTTPException

    from app.auth import Principal

    account_id = await _pension_account(
        super_engine,
        firm,
        product="SIPP",
        link_contact=False,
        administer=True,
        link_scheme=True,
    )
    called = False

    async def _never(*_a: object, **_k: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(crm_router.morgan_lloyd, "drawdown_session", _never)
    async with _authed(db["app"], str(firm["all_wos"]), ["instructions:write"]) as session:
        with pytest.raises(HTTPException) as exc:
            await crm_router.account_drawdown_session(
                account_id=account_id,
                principal=Principal(
                    user_id=str(firm["all_wos"]), permissions=["instructions:write"]
                ),
                session=session,
            )
    assert exc.value.status_code == 409
    assert "client is not linked" in str(exc.value.detail).lower()
    assert not called, "an unlinked client must not reach Morgan Lloyd"


@pytest.mark.asyncio
async def test_drawdown_sends_a_ctc1_illustration_template(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DCube retrieves the illustration on the benefit application after SSO.

    A mint without `illustrationTemplate` still returns an SSO URL (OpenAPI marks it optional);
    the journey then has no illustration document. The template is CTC1 XML, not a prior filed
    illustration, and there are still no CTC credentials on this path — Morgan Lloyd stores the
    XML and DCube reads it back via `cam_memberdetails`.
    """
    from app.auth import Principal

    account_id = await _pension_account(
        super_engine,
        firm,
        product="SIPP",
        link_contact=True,
        administer=True,
        link_scheme=True,
    )
    sent: dict[str, object] = {}

    async def _capture(
        contact_guid: str,
        *,
        scheme_ref: str | None = None,
        asset_valuation: float | None = None,
        **rest: object,
    ) -> dict[str, object]:
        sent.update(contact=contact_guid, scheme_ref=scheme_ref, value=asset_valuation, **rest)
        return {"sso_url": "https://dcube.example/s", "benefit_application_id": "ba_1"}

    monkeypatch.setattr(crm_router.morgan_lloyd, "drawdown_session", _capture)
    async with _authed(db["app"], str(firm["all_wos"]), ["instructions:write"]) as session:
        out = await crm_router.account_drawdown_session(
            account_id=account_id,
            principal=Principal(user_id=str(firm["all_wos"]), permissions=["instructions:write"]),
            session=session,
        )

    assert out["sso_url"] == "https://dcube.example/s"
    assert str(sent["contact"]).startswith("ML_GUID_")
    assert str(sent["scheme_ref"]).startswith("MLSIPP/")
    assert sent["value"] == 50000.0, "the account's own value is the asset valuation"
    xml = str(sent["illustration_template"])
    assert "<message_version>CTC1</message_version>" in xml
    assert "<quotation_type>Flexi Access Drawdown</quotation_type>" in xml
    assert "Ashworth" in xml, "the client's surname is what DCube shows as the member"
    assert "cash_valuation" not in sent, (
        "no cash row is omitted, not invented as a percentage of the pot"
    )


@pytest.mark.asyncio
async def test_drawdown_proceeds_when_the_account_is_synced_even_if_the_shelf_is_silent(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sync is the fact that this is an ML scheme, not the product's platform mapping.

    A real SIPP's shelf often names only the custodian. Waiting for ``scheme_administrator`` on
    ``product_platforms`` 409'd every synced account whose product had never been tagged, with a
    message that read as if Morgan Lloyd had never heard of it. The scheme reference and contact
    GUID ``Sync to Morgan Lloyd`` writes are what ``cam_setmemberdetails`` addresses.
    """
    from app.auth import Principal

    account_id = await _pension_account(
        super_engine,
        firm,
        product="SIPP",
        link_contact=True,
        administer=False,
        link_scheme=True,
    )
    seen: dict[str, object] = {}

    async def _fake_drawdown(contact_guid: str, **kwargs: object) -> dict[str, object]:
        seen.update(contact=contact_guid, **kwargs)
        return {"sso_url": "https://dcube.example/session"}

    monkeypatch.setattr(crm_router.morgan_lloyd, "drawdown_session", _fake_drawdown)
    async with _authed(db["app"], str(firm["all_wos"]), ["instructions:write"]) as session:
        out = await crm_router.account_drawdown_session(
            account_id=account_id,
            principal=Principal(user_id=str(firm["all_wos"]), permissions=["instructions:write"]),
            session=session,
        )
    assert out == {"sso_url": "https://dcube.example/session"}
    assert str(seen["contact"]).startswith("ML_GUID_")
    assert str(seen["scheme_ref"]).startswith("MLSIPP/")


@pytest.mark.asyncio
async def test_drawdown_refuses_when_this_account_has_no_scheme_link(
    db: dict[str, str],
    firm: dict[str, object],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """409, and no vendor call — even when the CLIENT already has an ML contact.

    That is the hole: a contact GUID from another scheme plus `scheme_ref=None` would start a
    live drawdown journey against this pot's valuation. The scheme reference must be THIS
    account's ``platform.links`` row.
    """
    from fastapi import HTTPException

    from app.auth import Principal

    account_id = await _pension_account(
        super_engine,
        firm,
        product="SIPP",
        link_contact=True,
        administer=True,
        link_scheme=False,
    )
    called = False

    async def _never(*_a: object, **_k: object) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(crm_router.morgan_lloyd, "drawdown_session", _never)
    async with _authed(db["app"], str(firm["all_wos"]), ["instructions:write"]) as session:
        with pytest.raises(HTTPException) as exc:
            await crm_router.account_drawdown_session(
                account_id=account_id,
                principal=Principal(
                    user_id=str(firm["all_wos"]), permissions=["instructions:write"]
                ),
                session=session,
            )
    assert exc.value.status_code == 409
    assert "account is not linked" in str(exc.value.detail).lower()
    assert not called, "an account with no scheme link must not reach Morgan Lloyd"


# ---- the client search box: who it is for, and what it looks at -------------------------------
# Two regressions, both reported off the same screen. The box is how you find a PERSON; the
# adviser dropdown beside it is how you narrow to a book, and conflating the two made the box
# useless for any firm whose adviser shares a surname with a client.


async def test_client_search_ignores_adviser_names(
    db: dict[str, str], firm: dict[str, object]
) -> None:
    """Searching an ADVISER's name returns none of their clients — that is the filter's job.

    `advisers` used to be spread into the searchable fields, so a firm with an adviser called
    Gaunt got that adviser's entire book back when they searched for the client called Gaunt.
    """
    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_contacts(session=session, q="Lead")
    # Zoe Lead advises the rich client; neither client is themselves called Lead.
    # No paging params, so this is the legacy bare list (docs/api.md).
    assert [str(r["surname"]) for r in rows] == []


async def test_client_search_matches_the_name_as_it_is_displayed(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """A title is part of the name on screen, so it has to be part of the name you can type.

    The listing renders `clientName()` — "Mr Mac Gaunt" — while the search looked at `first_name`
    and `surname` alone and matches the query as ONE contiguous substring. So typing the name
    exactly as shown found nothing, and the reporter had to guess that dropping the title worked.
    """
    with super_engine.connect() as c:
        c.execute(
            text("UPDATE crm.contacts SET title = 'Mr', first_name = 'Mac' WHERE id = :id"),
            {"id": int(firm["booked"])},
        )
        c.commit()

    async def find(query: str) -> list[str]:
        async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
            rows = await crm_router.list_contacts(session=session, q=query)
        return [str(r["surname"]) for r in rows]

    # The name as it is shown, the name without the title, and the parts on their own.
    assert await find("Mr Mac Beaumont") == ["Beaumont"]
    assert await find("Mac Beaumont") == ["Beaumont"]
    assert await find("beaumont") == ["Beaumont"]
    assert await find("mac") == ["Beaumont"]


# ---------------------------------------------------------------------------------------------
# The account's SERVICE TYPE (the accounts listing's Service column and its filter)


@pytest.mark.asyncio
async def test_account_service_type_is_only_the_captured_capacity(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """`service_type` is decided in SQL so the column, the filter, the facet and the sort cannot
    disagree about one account.

    The captured `platform.accounts.investment_capacity` (onboarding asks it) is the service.
    A model without a capacity is not a service agreement: SEI publishes a model id on accounts
    whose service it never stated, and inferring discretionary from that id would put an
    agreement on screen that nobody recorded. With neither, the answer is NULL.
    """
    tenant = int(firm["tenant"])
    with super_engine.connect() as c:
        provider = str(c.execute(text(SECCL_PLATFORM_SQL)).scalar_one())
        model_id = int(
            c.execute(
                text(
                    "INSERT INTO platform.models (tenant_id, provider, name) "
                    "VALUES (:t, :p, 'Growth 60') RETURNING id"
                ),
                {"t": tenant, "p": provider},
            ).scalar_one()
        )
        # The captured capacity, on the account that also carries a model — so we can prove the
        # capacity WINS rather than merely agreeing with the model.
        c.execute(
            text(
                "UPDATE platform.accounts SET investment_capacity = 'execution_only', "
                "  model_id = :m WHERE id = :id"
            ),
            {"id": int(firm["active_account"]), "m": model_id},
        )
        # A model and no capacity: the custodian-fed shape. Resolved rather than taken from the
        # fixture, which deliberately publishes only the account its other tests address.
        booked_account = int(
            c.execute(
                text("SELECT id FROM platform.accounts WHERE contact_id = :con"),
                {"con": int(firm["booked"])},
            ).scalar_one()
        )
        c.execute(
            text("UPDATE platform.accounts SET model_id = :m WHERE id = :id"),
            {"id": booked_account, "m": model_id},
        )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        rows = await crm_router.list_accounts(session=session)
        detail = await crm_router.get_account(
            account_id=int(firm["active_account"]), session=session
        )
        filtered = await crm_router.list_accounts(session=session, service="execution_only")
        # The THIRD reader: the client record shows a person's accounts in a table of their own.
        record = await crm_router.get_contact(contact_id=int(firm["rich"]), session=session)

    assert isinstance(rows, list)
    services = {int(row["id"]): row["service_type"] for row in rows}
    assert services[int(firm["active_account"])] == "execution_only"
    assert services[booked_account] is None
    # The draft and closed accounts have neither, and say so.
    assert None in services.values()

    # The detail endpoint answers with the same field, from the same expression.
    assert detail["service_type"] == "execution_only"

    # And the filter selects on exactly what the column shows.
    assert isinstance(filtered, list)
    assert [int(row["id"]) for row in filtered] == [int(firm["active_account"])]

    # All three surfaces read one expression, so they cannot disagree about one account.
    record_accounts = {int(acc["id"]): acc["service_type"] for acc in record["accounts"]}
    assert record_accounts[int(firm["active_account"])] == "execution_only"


# ---------------------------------------------------------------------------------------------
# The clients listing's two new answers: what KIND of client, and which WRAPPERS they hold


@pytest.mark.asyncio
async def test_contacts_listing_carries_the_client_kind_and_the_wrappers_held(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """`client_type` and `account_types`, and the filters over both.

    `client_type` collapses the two columns `crm.contacts` says it in — `type` plus the `sub_type`
    that 0002's CHECK allows only on an organisation — so a surface has one field to show. The
    `type` FILTER still matches either level, because the two are a hierarchy: asking for
    organisations must keep finding the trust.

    `account_types` is what the listing shows in place of a bare count, and it must come back as a
    real `text[]`: `product_type` is a custom enum and psycopg has no loader for an array of one,
    so an uncast `array_agg` arrives as the literal string `{GIA,ISA}` — which every type in the
    chain calls `string[]` while `.some()` on it throws.
    """
    tenant, rich, booked = int(firm["tenant"]), int(firm["rich"]), int(firm["booked"])
    with super_engine.connect() as c:

        def product(code: str, wrapper: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO platform.products (tenant_id, code, name, "
                        "  product_type) "
                        "VALUES (:t, :c, :n, CAST(:w AS platform.product_type)) "
                        "RETURNING id"
                    ),
                    {"t": tenant, "c": code, "n": code, "w": wrapper},
                ).scalar_one()
            )

        sipp, isa = product(f"SIPP_{tenant}", "SIPP"), product(f"ISA_{tenant}", "ISA")
        # The rich client holds three accounts: two of them ISAs, so the tag list must DEDUPE.
        rows = [
            int(r[0])
            for r in c.execute(
                text("SELECT id FROM platform.accounts WHERE contact_id = :c ORDER BY id"),
                {"c": rich},
            ).all()
        ]
        for account_id, product_id in zip(rows, [sipp, isa, isa], strict=False):
            c.execute(
                text("UPDATE platform.accounts SET product_id = :p WHERE id = :id"),
                {"p": product_id, "id": account_id},
            )
        # The other client is a TRUST — an organisation with a sub-type.
        c.execute(
            text(
                "UPDATE crm.contacts SET type = 'organisation', "
                "  sub_type = CAST('trust' AS crm.contact_sub_type) WHERE id = :id"
            ),
            {"id": booked},
        )
        # And a postal address on the first, for the line the listing prints under a name that
        # has no date of birth to show.
        c.execute(
            text("UPDATE crm.contacts SET address = CAST(:address AS jsonb) WHERE id = :id"),
            {
                "id": rich,
                "address": json.dumps(
                    {
                        "line1": "1 Meneage St",
                        "city": "Helston",
                        "postcode": "TR13 9PU",
                        "country": "GB",
                    }
                ),
            },
        )
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        listed = await crm_router.list_contacts(session=session)
        organisations = await crm_router.list_contacts(session=session, type="organisation")
        trusts = await crm_router.list_contacts(session=session, type="trust")
        individuals = await crm_router.list_contacts(session=session, type="individual")
        with_sipp = await crm_router.list_contacts(session=session, account_type="SIPP")
        with_gia = await crm_router.list_contacts(session=session, account_type="GIA")

    assert isinstance(listed, list)
    by_id = {int(row["id"]): row for row in listed}

    # A real list of strings, deduped and sorted — not the raw enum-array literal.
    wrappers = by_id[rich]["account_types"]
    assert isinstance(wrappers, list)
    assert wrappers == ["ISA", "SIPP"]
    # The count is untouched: three accounts, two distinct wrappers.
    assert by_id[rich]["account_count"] == 3

    # The kind, at its most specific for the organisation and unchanged for the individual.
    assert by_id[rich]["client_type"] == "individual"
    assert by_id[booked]["client_type"] == "trust"

    # The address rides along as the stored object, null where none is stored — the listing
    # decides how much of it to print.
    assert by_id[rich]["address"] == {
        "line1": "1 Meneage St",
        "city": "Helston",
        "postcode": "TR13 9PU",
        "country": "GB",
    }
    assert by_id[booked]["address"] is None

    # BOTH levels select the trust; the specific one does not select the individual.
    assert [int(r["id"]) for r in organisations] == [booked]  # type: ignore[union-attr]
    assert [int(r["id"]) for r in trusts] == [booked]  # type: ignore[union-attr]
    assert [int(r["id"]) for r in individuals] == [rich]  # type: ignore[union-attr]

    # And the wrapper filter is a set test: the client holding a SIPP, and nobody for a wrapper
    # nobody holds.
    assert [int(r["id"]) for r in with_sipp] == [rich]  # type: ignore[union-attr]
    assert list(with_gia) == []


@pytest.mark.asyncio
async def test_custodian_filter_is_the_provider_holding_the_account(
    db: dict[str, str], firm: dict[str, object], super_engine: Engine
) -> None:
    """The same provider code on clients, accounts and transactions.

    A client matches when any of their accounts is held there. The account's own column wins;
    where it is absent, the primary custodian link is the fallback the drawer already uses, so
    the filter and the record cannot name two different providers.
    """
    tenant, rich, booked = int(firm["tenant"]), int(firm["rich"]), int(firm["booked"])
    active = int(firm["active_account"])
    with super_engine.connect() as c:
        booked_account = int(
            c.execute(
                text(
                    "SELECT id FROM platform.accounts "
                    "WHERE contact_id = :c AND uid LIKE 'crmacc_booked_%'"
                ),
                {"c": booked},
            ).scalar_one()
        )
        c.execute(
            text("UPDATE platform.accounts SET custodian = 'seccl' WHERE id = :id"),
            {"id": active},
        )
        c.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, link_type, external_id) "
                "VALUES (:t, 'account', :id, 'sei', 'primary', :ext)"
            ),
            {"t": tenant, "id": booked_account, "ext": f"SEI_{booked_account}"},
        )

        def movement(account_id: int, source: str) -> None:
            c.execute(
                text(
                    "INSERT INTO platform.transactions "
                    "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                    "   amount, currency, transaction_date) "
                    "VALUES (:t, :a, :src, :sid, 'deposit', 10, 'GBP', CURRENT_DATE)"
                ),
                {
                    "t": tenant,
                    "a": account_id,
                    "src": source,
                    "sid": f"CUST_{source}_{account_id}",
                },
            )

        movement(active, "seccl")
        movement(booked_account, "sei")
        c.commit()

    async with _authed(db["app"], str(firm["all_wos"]), ["contacts:read_all"]) as session:
        seccl_clients = await crm_router.list_contacts(
            session=session, custodian=["seccl"], limit=25
        )
        sei_clients = await crm_router.list_contacts(session=session, custodian=["sei"], limit=25)
        none = await crm_router.list_contacts(session=session, custodian=["morgan-lloyd"], limit=25)
        seccl_accounts = await crm_router.list_accounts(
            session=session, custodian=["seccl"], limit=25
        )
        sei_accounts = await crm_router.list_accounts(session=session, custodian=["sei"], limit=25)
        sei_tx = await crm_router.list_transactions(session=session, custodian=["sei"], paged=True)

    assert isinstance(seccl_clients, dict)
    assert [int(row["id"]) for row in seccl_clients["items"]] == [rich]  # type: ignore[index]
    assert seccl_clients["items"][0]["custodians"] == ["seccl"]  # type: ignore[index]
    assert [int(row["id"]) for row in sei_clients["items"]] == [booked]  # type: ignore[index, union-attr]
    assert sei_clients["items"][0]["custodians"] == ["sei"]  # type: ignore[index]
    assert sei_clients["facets"]["custodian"] == ["seccl", "sei"]  # type: ignore[index]
    assert none["items"] == []  # type: ignore[index]

    assert [int(row["id"]) for row in seccl_accounts["items"]] == [active]  # type: ignore[index, union-attr]
    assert seccl_accounts["items"][0]["custodian"] == "seccl"  # type: ignore[index]
    assert [int(row["id"]) for row in sei_accounts["items"]] == [booked_account]  # type: ignore[index, union-attr]
    assert sei_accounts["items"][0]["custodian"] == "sei"  # type: ignore[index]

    assert isinstance(sei_tx, dict)
    assert [int(row["account_id"]) for row in sei_tx["items"]] == [booked_account]  # type: ignore[index]
    assert sei_tx["items"][0]["custodian"] == "sei"  # type: ignore[index]
    assert "sei" in sei_tx["facets"]["custodian"]  # type: ignore[operator]


class TestOneAccountHasOneReturn:
    """The accounts listing showed -11.6% for an account whose own record page said +856.3%, on
    the same account on the same day. The listing measured movement over the tracked value series;
    the record summed the holdings' book cost. Whichever is more defensible, a client cannot be
    shown both — so both now call `_series_return`."""

    def test_the_rule_is_movement_over_the_series(self) -> None:
        assert crm_router._series_return([100.0, 110.0]) == (10.0, 0.1)
        assert crm_router._series_return([200.0, 150.0]) == (-50.0, -0.25)

    def test_both_ends_come_from_the_series(self) -> None:
        """Not "series start to summed holdings": `_firm_holdings` drops unsettled negative legs
        and the series does not, so mixing the bases would read a filter as performance."""
        assert crm_router._series_return([100.0, 120.0, 130.0]) == (30.0, 0.3)

    def test_no_basis_is_not_a_flat_return(self) -> None:
        assert crm_router._series_return([]) == (0.0, None)
        assert crm_router._series_return([500.0]) == (0.0, None)  # one point is not a series
        assert crm_router._series_return([0.0, 900.0]) == (0.0, None)  # nothing to return ON
        assert crm_router._series_return([-10.0, 900.0]) == (0.0, None)

    def test_it_never_reads_book_cost(self) -> None:
        """The whole point: the custodian's cost basis yields +12,941% on a real row, so a
        headline derived from it is an artefact rather than a return."""
        import ast
        import inspect

        source = inspect.getsource(crm_router._series_return)
        names = {
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        assert not any("book_cost" in n or "growth" in n for n in names)


def test_holding_return_does_not_claim_flat_when_cost_basis_is_unusable() -> None:
    assert crm_router._holding_return({"book_cost": None, "growth": 10}) is None
    assert crm_router._holding_return({"book_cost": 1, "growth": 11}) is None
    assert crm_router._holding_return({"book_cost": 100, "growth": 10}) == 0.1


def test_client_holding_prints_currency_as_cash_and_leaves_a_fund_alone() -> None:
    """Pound Sterling with a unit count is the cash balance, not a fund. A money-market fund
    stays an investment — the wider cash rule is for withdrawals, not this list."""
    cash = crm_router._client_holding(
        {
            "asset_name": "Pound Sterling",
            "asset_type": "Cash",
            "isin": None,
            "quantity": 693.644,
            "price": 1,
            "value": 693.64,
            "book_cost": 693.64,
            "growth": 0,
        },
        "GIA",
    )
    assert cash == {
        "name": "Cash",
        "ticker": None,
        "wrapper": "GIA",
        "units": 0,
        "price": 0,
        "value": 693.64,
        "gainPct": None,
        "cash": True,
    }
    fund = crm_router._client_holding(
        {
            "asset_name": "Royal London Short Term Money Market Y Acc",
            "asset_type": "Stock",
            "isin": "GB00B8GG8X16",
            "quantity": 120.5,
            "price": 1.42,
            "value": 171.11,
            "book_cost": 150,
            "growth": 21.11,
        },
        "GIA",
    )
    assert fund["name"] == "Royal London Short Term Money Market Y Acc"
    assert fund["units"] == 120.5
    assert "cash" not in fund
