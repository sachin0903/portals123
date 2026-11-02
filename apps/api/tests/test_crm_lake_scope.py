"""Lake metrics on listings must not read the whole book for a 25-row page."""

import pytest

from app.routers import crm
from app.routers.crm import restrict_visible


def test_restrict_visible_keeps_only_the_paged_contacts() -> None:
    visible = {
        "seccl-a": {"account_id": 1, "contact_id": 10},
        "seccl-b": {"account_id": 2, "contact_id": 11},
        "seccl-c": {"account_id": 3, "contact_id": 10},
    }
    narrowed = restrict_visible(visible, contact_ids={10})
    assert set(narrowed) == {"seccl-a", "seccl-c"}


def test_restrict_visible_keeps_only_the_paged_accounts() -> None:
    visible = {
        "seccl-a": {"account_id": 1, "contact_id": 10},
        "seccl-b": {"account_id": 2, "contact_id": 11},
    }
    narrowed = restrict_visible(visible, account_ids={2})
    assert set(narrowed) == {"seccl-b"}


def test_restrict_visible_empty_id_set_reads_nothing() -> None:
    visible = {"seccl-a": {"account_id": 1, "contact_id": 10}}
    assert restrict_visible(visible, contact_ids=set()) == {}


@pytest.mark.asyncio
async def test_firm_read_uses_each_accounts_own_provider_and_firm() -> None:
    calls: list[tuple[str, str, list[str]]] = []

    async def reader(provider: str, tenant_uid: str, ids: list[str]) -> list[dict[str, object]]:
        calls.append((provider, tenant_uid, ids))
        return [{"account_external_id": ids[0]}]

    rows = await crm._firm_read(
        reader,
        {
            "seccl:same": {
                "provider": "seccl",
                "tenant_uid": "ten_one",
                "external_id": "same",
            },
            "sei:same": {
                "provider": "sei",
                "tenant_uid": "ten_two",
                "external_id": "same",
            },
        },
    )
    assert {(p, t, tuple(ids)) for p, t, ids in calls} == {
        ("seccl", "ten_one", ("same",)),
        ("sei", "ten_two", ("same",)),
    }
    assert {row["_provider"] for row in rows} == {"seccl", "sei"}


def test_get_contact_scopes_the_lake_to_that_client() -> None:
    import inspect

    source = inspect.getsource(crm.get_contact)
    assert "_visible_accounts(session, contact_id=contact_id)" in source


def test_list_accounts_reads_cash_from_the_stored_valuation() -> None:
    import inspect

    source = inspect.getsource(crm.list_accounts)
    assert "_LISTING_BOOK_CTES" in source
    assert "account_valuations AS MATERIALIZED" in crm._LISTING_BOOK_CTES
    assert "searched AS MATERIALIZED" in source
    assert "_listing_page_sql" in source
    assert "_with_account_metrics" not in source
    assert "_lake_sorted_ids" not in source


@pytest.mark.asyncio
async def test_lake_metrics_reads_the_summary_not_every_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contacts and accounts ask for two numbers. That is the summary, not the position list."""
    from app.lake import holdings as lake_holdings

    called = {"summary": 0, "positions": 0}

    async def summary(
        provider: str, tenant_uid: str, external_account_ids: list[str]
    ) -> list[dict[str, object]]:
        del provider, tenant_uid
        called["summary"] += 1
        assert external_account_ids == ["ACC"]
        return [
            {"account_external_id": "ACC", "klass": "Cash", "value": 10, "growth": 1},
            {"account_external_id": "ACC", "klass": "Equities", "value": 40, "growth": 4},
        ]

    async def positions(
        provider: str, tenant_uid: str, external_account_ids: list[str]
    ) -> list[dict[str, object]]:
        del provider, tenant_uid, external_account_ids
        called["positions"] += 1
        return [{"account_external_id": "ACC", "value": 999, "growth": 999, "asset_type": "Cash"}]

    monkeypatch.setattr(lake_holdings, "firm_holdings_summary", summary)
    monkeypatch.setattr(lake_holdings, "firm_holdings", positions)

    by_account, by_contact = await crm._lake_metrics(
        {
            "seccl:ACC": {
                "provider": "seccl",
                "tenant_uid": "ten_abc",
                "external_id": "ACC",
                "account_id": 7,
                "contact_id": 3,
                "seeded_demo": False,
            }
        }
    )
    assert called == {"summary": 1, "positions": 0}
    assert by_account[7] == {"cash": 10.0, "growth": 5.0}
    assert by_contact[3] == {"cash": 10.0, "growth": 5.0}


def test_list_contacts_reads_cash_from_the_stored_valuation() -> None:
    import inspect

    source = inspect.getsource(crm.list_contacts)
    assert "_LISTING_BOOK_CTES" in source
    assert "account_valuations AS MATERIALIZED" in crm._LISTING_BOOK_CTES
    assert "searched AS MATERIALIZED" in source
    assert "_listing_page_sql" in source
    assert "_with_contact_metrics" not in source
    # A NULL stays a NULL. Zero would claim the custodian stated a flat position.
    assert 'item["cash"] = None' not in source


def test_transaction_listing_has_no_prefetch_ceiling() -> None:
    """The ledger used to read ``LIMIT 2000`` then filter in Python. ``paged=true`` is a real
    page of the match set; a 25-row screen must not ship 2000 movements."""
    import inspect
    from pathlib import Path

    source = Path(crm.__file__).read_text(encoding="utf-8")
    assert "_TRANSACTION_FETCH_MAX" not in source
    tx = inspect.getsource(crm.list_transactions)
    assert "needs_action" in tx
    assert "LIMIT :lim" in tx


def test_visible_accounts_does_not_join_dropped_platforms_table() -> None:
    """0141 dropped `platform.platforms` and `accounts.primary_custodian`. The listing's lake
    resolver still joined both, so GET /crm/contacts 500'd on every book — indistinguishable from
    a pipeline that loaded no clients."""
    import inspect

    source = inspect.getsource(crm._visible_accounts)
    assert "platform.platforms" not in source
    assert "primary_custodian" not in source
    assert "a.custodian" in source
    assert "providers.custodians()" in source
    assert "seeded_demo" in source


@pytest.mark.asyncio
async def test_a_seeded_book_searches_holdings_in_the_demo_lake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spotlight used the provider catalogue for a book that lives in the demo lake.

    The holdings page calls ``firm_holdings``, which the demo reader map knew about, so the
    page listed positions. Search calls ``search_firm_holdings``, which the map did not, so
    the palette reported none.
    """
    from app.lake import holdings as lake_holdings

    seen: dict[str, object] = {}

    async def demo_search(
        provider: str, tenant_uid: str, ids: list[str], *, q: str, limit: int
    ) -> list[dict[str, object]]:
        seen["call"] = (provider, tenant_uid, tuple(ids), q, limit)
        return [{"account_external_id": ids[0], "asset_name": "Global Equity"}]

    monkeypatch.setattr("app.lake.demo.search_firm_holdings", demo_search)
    rows = await crm._firm_read(
        lake_holdings.search_firm_holdings,
        {
            "seccl:ACC": {
                "provider": "seccl",
                "tenant_uid": "ten_demo",
                "external_id": "ACC",
                "seeded_demo": True,
            }
        },
        q="global",
        limit=7,
    )
    assert seen["call"] == ("seccl", "ten_demo", ("ACC",), "global", 7)
    assert rows == [
        {"account_external_id": "ACC", "asset_name": "Global Equity", "_provider": "seccl"}
    ]


@pytest.mark.asyncio
async def test_an_unreadable_slice_searches_the_dev_extract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same hole on the extract fallback: the page fell back, search returned nothing."""
    from app.lake import core as lake_core
    from app.lake import fixture as lake_fixture
    from app.lake import holdings as lake_holdings

    lake_core.reset_pool()
    monkeypatch.setattr(lake_fixture, "available", lambda: True)

    seen: dict[str, object] = {}

    async def extract(ids: list[str], *, q: str, limit: int) -> list[dict[str, object]]:
        seen["call"] = (tuple(ids), q, limit)
        return [{"account_external_id": ids[0], "asset_name": "Global Equity", "value": 3}]

    monkeypatch.setattr(lake_fixture, "search_holdings", extract)

    def down(_provider: str) -> None:
        raise lake_core.CoreUnavailable("down")

    monkeypatch.setattr(lake_core, "connect", down)

    rows = await crm._firm_read(
        lake_holdings.search_firm_holdings,
        {
            "seccl:ACC": {
                "provider": "seccl",
                "tenant_uid": "ten_abc",
                "external_id": "ACC",
                "seeded_demo": False,
            }
        },
        q="equity",
        limit=7,
    )
    assert seen["call"] == (("ACC",), "equity", 7)
    assert rows[0]["_provider"] == "seccl"
    assert rows[0]["asset_name"] == "Global Equity"


def test_home_carries_the_demo_marker_into_its_lake_reads() -> None:
    """Demo accounts used the provider reader on home, emptying every lake-backed widget."""
    import inspect

    source = inspect.getsource(crm.home)
    assert "AS seeded_demo" in source
    assert "l.data->>'seeded_demo'" in source
    # A seeded administrator link (Graphene's morgan-lloyd) must still count as a home card.
    assert "OR COALESCE(l.data->>'seeded_demo' = 'true', false)" in source
