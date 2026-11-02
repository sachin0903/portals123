"""Scale behaviour of the client-book endpoints: /crm/home's ``wrappers`` modes and the
``limit``/``offset`` pagination on /crm/accounts + /crm/holdings.

A trust client can hold 300+ accounts, and /crm/home used to build a 60-point sparkline per
account regardless -- a payload nobody can render -- while the listing endpoints returned the
whole book or nothing. Same harness as ``test_crm_api.py`` (real ``authenticated`` session, RLS
decides visibility, the lake faked). What is pinned here:

  * ``wrappers=auto`` keeps today's full shape up to 25 valued accounts and flips to the
    summary payload at 26 -- the composed portal's own accounts-block boundary
    (VARIANT_THRESHOLDS.accountsCardsMax + 1, pkgs/ui .../branding/variants.ts);
  * the summary reconciles exactly with the book it summarises: group counts sum to
    ``wrappers_total``, group values to the valued book, and the cards are genuinely the top
    ten by value -- with every sparkline shed (``trend`` omitted, not emptied);
  * ``full`` -- and no param at all -- serialises identically to each other, and the response
    grew by exactly the documented provenance keys (``as_of`` / ``performance_period`` at the
    top level, ``value_as_of`` per wrapper card) with every pre-existing key's value untouched;
  * the summary payload is bounded (< 64 kB for a 300-account book) and nowhere near linear
    in the book -- the tripwire against sparklines creeping back into summary mode;
  * pagination clamps junk instead of erroring, pages walk the book without overlap or loss,
    ``total`` is the caller's own RLS-scoped count, and an absent ``limit`` leaves the bare
    list untouched for every existing caller;
  * the ``q`` search on both listings is server-side and composes with the paging: it filters
    the shaped rows case-insensitively (holdings: asset name + ISIN; accounts: account name +
    product type), ``total`` becomes the filtered match count, ``offset`` walks the FILTERED
    list, blank/whitespace ``q`` is a byte-identical no-op, bare-list mode honours it too, and
    RLS still bounds it -- a client's search never surfaces a sibling client's rows;
  * the toolbar the envelope carries: a numeric RANGE actually narrows (it was accepted and
    silently ignored, which is the worst way for a filter to be wrong), a value the book cannot
    see is unknown rather than zero, every facet option carries the tally taken over its own
    rows, and ``bounds`` are the column's real ends with only that range's own filter lifted.
"""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.lake import holdings as lake_holdings
from app.routers import crm as crm_router
from app.routers.crm import _PAGE_LIMIT_MAX, _WRAPPERS_SUMMARY_OVER, _WRAPPERS_SUMMARY_TOP
from tests.conftest import SECCL_PLATFORM_SQL

# The exact key set /crm/home returned before the July 2026 provenance stamps...
_LEGACY_HOME_KEYS = {
    "totals",
    "performance",
    "wrappers",
    "allocations",
    "holdings",
    "activity",
    "adviser",
}
# ...plus the provenance additions (additive, carried by EVERY mode): `as_of` at the top level
# and `performance_period`; each wrapper card gains `value_as_of` (see _CARD_KEYS).
_HOME_PROVENANCE_KEYS = {"as_of", "performance_period"}
_FULL_HOME_KEYS = _LEGACY_HOME_KEYS | _HOME_PROVENANCE_KEYS
_SUMMARY_ONLY_KEYS = {"wrapper_groups", "wrappers_total", "wrappers_summarised"}

# A full-mode wrapper card, exactly; a summary card is the same minus `trend`.
_CARD_KEYS = {"type", "name", "owner", "value", "value_as_of", "gain", "gainPct", "trend", "href"}

# Product types cycle per account index i (1-based): SQL reads (ARRAY['ISA','SIPP','GIA',NULL])
# [(i % 4) + 1], which is this tuple at [i % 4]. None exercises the 'Account' fallback.
_PTYPES: tuple[str | None, ...] = ("ISA", "SIPP", "GIA", None)

# A paged listing's envelope, exactly. `facets` joined it when filtering moved server-side; the
# tally beside each option and a range column's real ends are the rest of what the shared filter
# panel draws, and a listing that omits one draws numbers on one page and none on the next.
_ENVELOPE_KEYS = {"items", "total", "facets", "facet_counts", "bounds"}

# App-only portal (0107): one application client id per portal surface, shared by every fixture
# tenant in this module — resolve_claims matches an app id against the CALLER'S OWN tenant's
# environment row, so reusing the strings across tenants is safe.
_APP_CLIENT = "app_scl_client"
_APP_PLATFORM = "app_scl_platform"


@asynccontextmanager
async def _authed(
    app_url: str, workos_user: str, perms: list[str], role: str = "adviser"
) -> AsyncIterator[AsyncSession]:
    """A session acting as a request principal -- mirrors test_crm_api._authed (and through it
    db.py::tenant_session): SET ROLE authenticated + claims minted by auth.resolve_claims.
    ``role='client'`` signs in through the CLIENT application (0107), so the minted portal claim
    is 'client' — which empties auth.tenant_lineage()."""
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


def _mk_book(super_engine: Engine, n: int) -> dict[str, object]:
    """A firm with one read-all staff login and one trust client holding ``n`` active,
    Seccl-linked accounts. Values are 1000*i (every account distinct, so "top by value" is
    provable) and product types cycle ``_PTYPES`` (so the summary's grouping, and its
    'Account' fallback for a NULL type, are both exercised). The staff login sits on no
    client's book, so /crm/home's adviser lookup never reaches its service_role branch."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:
        tenant = int(
            c.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id"
                ),
                {"u": f"sclten_{s}", "a": f"sclfirm-{s}", "n": f"ScaleFirm {s}"},
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
            {"t": tenant, "env": f"env_scl_{s}", "capp": _APP_CLIENT, "papp": _APP_PLATFORM},
        )
        wos = f"sclwos_staff_{s}"
        uid = str(
            c.execute(
                text(
                    "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                    "  first_name, last_name, enabled) "
                    "VALUES (:uid, :t, :email, :wos, 'Scale', 'Staff', true) RETURNING id"
                ),
                {
                    "uid": f"sclusr_staff_{s}",
                    "t": tenant,
                    "email": f"staff_{s}@scl.test",
                    "wos": wos,
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
        contact = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, service_type) "
                    "VALUES (:t, :uid, 'organisation', :n, 'advised') RETURNING id"
                ),
                {"t": tenant, "uid": f"sclcon_{s}", "n": f"Sizeable Trust {s}"},
            ).scalar_one()
        )
        # THE SHELF, not a jsonb key. The wrapper used to be seeded as
        # `data->>'seccl_product_type'`, which nothing but the legacy `ingest_seccl.py` writes —
        # so this fixture proved the grouping against the one shape no writer produces, exactly
        # as the `products.platform_id` fixture did. A pipeline-fed book has `product_id`.
        c.execute(
            text(
                "INSERT INTO platform.products (tenant_id, product_type, name, code, enabled) "
                "SELECT :t, w::platform.product_type, w, w, true "
                "  FROM unnest(ARRAY['ISA','SIPP','GIA']) AS w"
            ),
            {"t": tenant},
        )
        # The whole book in two statements -- a 300-account fixture must not be 600 inserts.
        c.execute(
            text(
                "INSERT INTO platform.accounts "
                "  (tenant_id, contact_id, uid, status, value, product_id) "
                "SELECT :t, :con, 'sclacc_' || :s || '_' || i, 'active', (1000 * i)::numeric, "
                "       (SELECT p.id FROM platform.products p "
                "         WHERE p.tenant_id = :t "
                # The fourth is NULL on purpose: an account with no shelf item is a real state
                # (98 of 379 on the dev book) and must group as "Account", not vanish.
                "           AND p.product_type::text "
                "                 = (ARRAY['ISA','SIPP','GIA',NULL])[(i % 4) + 1]) "
                "FROM generate_series(1, :n) AS g(i)"
            ),
            {"t": tenant, "con": contact, "s": s, "n": n},
        )
        c.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, external_id) "
                "SELECT :t, 'account', a.id, 'seccl', 'SCL_' || :s || '_' || a.id "
                "FROM platform.accounts a WHERE a.tenant_id = :t"
            ),
            {"t": tenant, "s": s},
        )
        ids = [
            int(r[0])
            for r in c.execute(
                text("SELECT id FROM platform.accounts WHERE tenant_id = :t ORDER BY id"),
                {"t": tenant},
            )
        ]
        c.commit()
    return {
        "tenant": tenant,
        "suffix": s,
        "wos": wos,
        "contact": contact,
        "account_ids": ids,
        "exts": {aid: f"SCL_{s}_{aid}" for aid in ids},
    }


def _expected_cards(n: int) -> list[tuple[float, str]]:
    """(value, displayed product type) per account i=1..n -- the Python mirror of _mk_book's
    generate_series, so the tests compute their expectations independently of the endpoint."""
    return [(1000.0 * i, _PTYPES[i % 4] or "Account") for i in range(1, n + 1)]


def _fake_lake_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No lake presence: home's wrapper values then fall back to the OLTP account values,
    which is exactly the figure _mk_book controls."""

    async def _holdings(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        return []

    async def _txns(
        provider: str, uid: str, ids: list[str], limit: int = 100
    ) -> list[dict[str, object]]:
        return []

    async def _series(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(lake_holdings, "firm_holdings", _holdings)
    monkeypatch.setattr(lake_holdings, "firm_transactions", _txns)
    monkeypatch.setattr(lake_holdings, "firm_value_series", _series)


@pytest.mark.asyncio
async def test_a_wrapper_card_links_by_uid_not_by_serial(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`href` is a URL, so it carries the account's UID.

    It carried `a['id']` -- the internal serial -- into `/accounts/{id}`, which the client
    portal's `/accounts/[uid]` route then received. The whole point of uid addressing is that a
    serial never appears in a URL, and this is the one place in the API that BUILDS a portal path
    rather than answering on one, so no route-shape scan could see it.

    Asserted against the account's real uid rather than a regex, because a 12-character nanoid of
    `[A-Za-z0-9]` matches a stringified serial for any book of fewer than a trillion accounts --
    a shape test would have passed on the bug.
    """
    _fake_lake_empty(monkeypatch)

    book = _mk_book(super_engine, 3)
    with super_engine.connect() as conn:
        conn.execute(text("SET ROLE service_role"))
        uids = {
            row[0]
            for row in conn.execute(
                text("SELECT uid FROM platform.accounts WHERE contact_id = :c"),
                {"c": book["contact"]},
            )
        }
    assert uids, "the fixture built no accounts -- the assertion below would be vacuous"

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.home(wrappers="full", session=session)

    hrefs = [w["href"] for w in out["wrappers"]]  # type: ignore[union-attr,index]
    assert hrefs, "no wrapper cards -- nothing was asserted"
    for href in hrefs:
        assert href.startswith("/accounts/"), href
        assert href.removeprefix("/accounts/") in uids, (
            f"{href} does not name one of this client's account uids {sorted(uids)} -- "
            "a serial in a URL is the bug this pins"
        )


# ---------------------------------------------------------------------------
# /crm/home?wrappers=...
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrappers_auto_flips_exactly_at_the_ui_boundary(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """25 valued accounts + auto => today's full shape (a card per account, sparkline and all);
    26 => summarised (top ten cards, no trends, the three summary keys). The boundary is the
    composed portal's accounts-block one, so both sides of it are pinned."""
    _fake_lake_empty(monkeypatch)

    at_boundary = _mk_book(super_engine, _WRAPPERS_SUMMARY_OVER)  # 25
    async with _authed(db["app"], str(at_boundary["wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.home(wrappers="auto", session=session)
    assert set(out) == _FULL_HOME_KEYS  # no summary keys at the boundary
    assert len(out["wrappers"]) == _WRAPPERS_SUMMARY_OVER  # type: ignore[arg-type]
    assert all("trend" in w for w in out["wrappers"])  # type: ignore[union-attr]

    over = _mk_book(super_engine, _WRAPPERS_SUMMARY_OVER + 1)  # 26
    async with _authed(db["app"], str(over["wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.home(wrappers="auto", session=session)
    assert set(out) == _FULL_HOME_KEYS | _SUMMARY_ONLY_KEYS
    assert out["wrappers_summarised"] is True
    assert len(out["wrappers"]) <= _WRAPPERS_SUMMARY_TOP  # type: ignore[arg-type]
    assert all("trend" not in w for w in out["wrappers"])  # type: ignore[union-attr]
    # The provenance stamp survives summarisation -- it is one small key, not the trend bloat.
    assert all(w["value_as_of"] is not None for w in out["wrappers"])  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_wrappers_summary_reconciles_exactly_with_the_book(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The digest must add up to the book it replaces: group counts sum to ``wrappers_total``
    (= every valued account), group values sum to the valued book to the penny, groups come
    value-desc, and the ten surviving cards are genuinely the ten biggest accounts."""
    _fake_lake_empty(monkeypatch)
    n = 40
    book = _mk_book(super_engine, n)
    expected = _expected_cards(n)

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.home(wrappers="summary", session=session)

    groups = out["wrapper_groups"]
    assert out["wrappers_total"] == n
    assert sum(g["count"] for g in groups) == out["wrappers_total"]  # type: ignore[union-attr]
    assert sum(g["value"] for g in groups) == pytest.approx(  # type: ignore[union-attr]
        round(sum(v for v, _ in expected), 2)
    )
    # Grouping matches the cards' own product-type fallback, per type.
    by_type: dict[str, tuple[int, float]] = {}
    for v, t in expected:
        count, value = by_type.get(t, (0, 0.0))
        by_type[t] = (count + 1, value + v)
    assert {g["product_type"]: (g["count"], g["value"]) for g in groups} == {  # type: ignore[union-attr]
        t: (count, pytest.approx(round(value, 2))) for t, (count, value) in by_type.items()
    }
    values = [g["value"] for g in groups]  # type: ignore[union-attr]
    assert values == sorted(values, reverse=True)

    # The cards are the top ten by value -- the mirror knows the values are 1000*i.
    top = sorted((v for v, _ in expected), reverse=True)[:_WRAPPERS_SUMMARY_TOP]
    assert [w["value"] for w in out["wrappers"]] == top  # type: ignore[union-attr]
    assert all("trend" not in w for w in out["wrappers"])  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_wrappers_summary_is_explicit_even_below_the_threshold(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``summary`` is a request, not a suggestion: a five-account book asked for summary gets
    the summary shape (all five cards survive -- fewer than the top-ten cap -- but trendless
    and with the summary keys)."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 5)
    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.home(wrappers="summary", session=session)
    assert set(out) == _FULL_HOME_KEYS | _SUMMARY_ONLY_KEYS
    assert out["wrappers_total"] == 5
    assert len(out["wrappers"]) == 5  # type: ignore[arg-type]
    assert all(set(w) == _CARD_KEYS - {"trend"} for w in out["wrappers"])  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_wrappers_full_is_byte_identical_to_the_no_param_response(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing callers never see this feature: ``wrappers=full`` -- on a book big enough that
    ``auto`` WOULD summarise -- serialises identically to the parameterless call, summary keys
    and all (i.e. none)."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 30)
    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        bare = await crm_router.home(session=session)
        full = await crm_router.home(wrappers="full", session=session)
    assert set(bare) == _FULL_HOME_KEYS
    assert json.dumps(full, default=str) == json.dumps(bare, default=str)


@pytest.mark.asyncio
async def test_home_grew_by_exactly_the_provenance_keys(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The successor to strict byte-identity now the shape has (deliberately) grown: the top
    level gains exactly ``as_of`` + ``performance_period``, each full-mode card gains exactly
    ``value_as_of`` -- and every pre-existing key still carries the value the fixture mirror
    predicts, so the additions demonstrably changed nothing they sit beside."""
    _fake_lake_empty(monkeypatch)
    n = 5
    book = _mk_book(super_engine, n)
    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        out = await crm_router.home(session=session)
        summary = await crm_router.home(wrappers="summary", session=session)

    # The additions, exactly -- no other key appeared at either level.
    assert set(out) - _LEGACY_HOME_KEYS == _HOME_PROVENANCE_KEYS
    assert all(set(w) == _CARD_KEYS for w in out["wrappers"])  # type: ignore[union-attr]
    assert all(set(w) == _CARD_KEYS - {"trend"} for w in summary["wrappers"])  # type: ignore[union-attr]

    # The pre-existing keys' values are what they always were (the fixture mirror, computed
    # independently of the endpoint): value-desc cards, an OLTP-summed total, no lake series.
    expected = _expected_cards(n)
    assert [w["value"] for w in out["wrappers"]] == sorted(  # type: ignore[union-attr]
        (v for v, _ in expected), reverse=True
    )
    assert out["totals"] == {
        "value": pytest.approx(round(sum(v for v, _ in expected), 2)),
        "gain": 0.0,
        "gainPct": None,
    }
    assert out["performance"] == []
    assert out["allocations"] == []
    assert out["holdings"] == []
    assert out["activity"] == []
    assert out["adviser"] is None

    # ...and the additions carry data: with no series the stamp comes from the accounts alone
    # (this fixture writes no `valued_at`, so `_VALUED_AT` falls back to the rows' updated_at)
    # and there is no chart basis to state.
    assert out["as_of"] is not None
    assert out["performance_period"] is None


@pytest.mark.asyncio
async def test_the_summary_payload_is_bounded_not_linear(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the mode, as a tripwire: a 300-account book with a 60-point value
    series per account must summarise to well under 64 kB AND well under a fifth of the full
    response for the same book -- if someone re-adds per-account sparklines to summary mode,
    this is the test that goes red."""

    async def _holdings(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        return []

    async def _txns(
        provider: str, uid: str, ids: list[str], limit: int = 100
    ) -> list[dict[str, object]]:
        return []

    async def _series(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        # 60 daily points per account, on shared dates (so `performance` stays bounded too).
        return [
            {
                "account_external_id": e,
                "date": f"2026-{(d // 28) + 1:02d}-{(d % 28) + 1:02d}",
                "value": 50.0 + d,
            }
            for e in ids
            for d in range(60)
        ]

    monkeypatch.setattr(lake_holdings, "firm_holdings", _holdings)
    monkeypatch.setattr(lake_holdings, "firm_transactions", _txns)
    monkeypatch.setattr(lake_holdings, "firm_value_series", _series)

    book = _mk_book(super_engine, 300)
    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        full = await crm_router.home(session=session)
        summary = await crm_router.home(wrappers="summary", session=session)

    assert len(full["wrappers"]) == 300  # type: ignore[arg-type]  # the wall being replaced
    summary_bytes = len(json.dumps(summary, default=str))
    full_bytes = len(json.dumps(full, default=str))
    assert summary_bytes < 64_000
    assert summary_bytes < full_bytes / 5


# ---------------------------------------------------------------------------
# /crm/accounts + /crm/holdings pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accounts_pagination_shape_and_clamps(
    db: dict[str, str], super_engine: Engine
) -> None:
    """No ``limit`` => the bare list every existing caller parses. With one => the
    ``{items, total}`` envelope, where the items are the same dicts in the same (a.id) order
    and junk bounds clamp rather than error: limit into 1..200, a negative offset to 0."""
    book = _mk_book(super_engine, 30)
    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        bare = await crm_router.list_accounts(session=session)
        page = await crm_router.list_accounts(limit=10, session=session)
        floor = await crm_router.list_accounts(limit=0, session=session)
        negative = await crm_router.list_accounts(limit=-3, offset=-5, session=session)
        beyond = await crm_router.list_accounts(limit=10, offset=1000, session=session)

    assert isinstance(bare, list) and len(bare) == 30
    assert [int(r["id"]) for r in bare] == list(book["account_ids"])  # deterministic order

    # `facets` joined the envelope when filtering moved server-side: the toolbar's options have
    # to come from the whole match set, not from the page on screen, or a filter becomes a
    # one-way door (app/listing.py).
    assert set(page) == _ENVELOPE_KEYS
    assert page["total"] == 30
    assert page["items"] == bare[:10]  # the page IS the head of the bare list, dicts and all

    assert len(floor["items"]) == 1 and floor["items"] == bare[:1]  # limit 0 clamps up to 1
    assert negative["items"] == bare[:1]  # limit -3 -> 1, offset -5 -> 0
    assert beyond["items"] == [] and beyond["total"] == 30  # off the end is empty, not an error


@pytest.mark.asyncio
async def test_accounts_pages_walk_the_whole_book_without_overlap(
    db: dict[str, str], super_engine: Engine
) -> None:
    """Pages must tile the book: with 250 accounts, an oversized limit clamps to 200 and the
    next offset picks up exactly where the first page stopped -- every account once, none
    twice, in the same order the bare list ships."""
    book = _mk_book(super_engine, 250)
    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        first = await crm_router.list_accounts(limit=999, session=session)
        second = await crm_router.list_accounts(limit=999, offset=_PAGE_LIMIT_MAX, session=session)

    assert len(first["items"]) == _PAGE_LIMIT_MAX  # 999 clamps to the page cap
    assert len(second["items"]) == 250 - _PAGE_LIMIT_MAX
    assert first["total"] == second["total"] == 250
    walked = [int(r["id"]) for r in first["items"]] + [int(r["id"]) for r in second["items"]]
    assert walked == list(book["account_ids"])  # no overlap, no loss, stable order


def _mk_sibling_client(super_engine: Engine, book: dict[str, object]) -> dict[str, object]:
    """The two-client fixture the RLS assertions stand on: a second ('solo') client of
    ``_mk_book``'s firm with one active, Seccl-linked, distinctively named account, plus a
    client-portal login for each of the two clients. Same tenant, disjoint books -- so anything
    one login can surface of the other's rows is a leak, not a fixture accident."""
    tenant, s = int(book["tenant"]), str(book["suffix"])
    with super_engine.connect() as c:
        other = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, surname, service_type) "
                    "VALUES (:t, :uid, 'individual', 'Onecount', 'advised') RETURNING id"
                ),
                {"t": tenant, "uid": f"sclcon_other_{s}"},
            ).scalar_one()
        )
        other_account = int(
            c.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, contact_id, uid, name, status, value) "
                    "VALUES (:t, :con, :uid, 'Solo Secret Reserve', 'active', 500) RETURNING id"
                ),
                {"t": tenant, "con": other, "uid": f"sclacc_other_{s}"},
            ).scalar_one()
        )
        other_ext = f"SCL_{s}_{other_account}"
        c.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, external_id) "
                "VALUES (:t, 'account', :a, 'seccl', :e)"
            ),
            {"t": tenant, "a": other_account, "e": other_ext},
        )

        def mk_client_login(tag: str, contact: int) -> str:
            wos = f"sclwos_{tag}_{s}"
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                        "  first_name, last_name, enabled) "
                        "VALUES (:uid, :t, :email, :wos, 'Scl', :last, true) RETURNING id"
                    ),
                    {
                        "uid": f"sclusr_{tag}_{s}",
                        "t": tenant,
                        "email": f"{tag}_{s}@scl.test",
                        "wos": wos,
                        "last": tag.capitalize(),
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, 'client')"
                ),
                {"u": uid, "t": tenant},
            )
            c.execute(
                text(
                    "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id, capacity) "
                    "VALUES (:t, :con, :u, 'self')"
                ),
                {"t": tenant, "con": contact, "u": uid},
            )
            return wos

        trust_wos = mk_client_login("trust", int(book["contact"]))
        solo_wos = mk_client_login("solo", other)
        c.commit()
    return {
        "trust_wos": trust_wos,
        "solo_wos": solo_wos,
        "solo_contact": other,
        "solo_account": other_account,
        "solo_ext": other_ext,
    }


@pytest.mark.asyncio
async def test_accounts_total_is_the_callers_own_rls_scoped_count(
    db: dict[str, str], super_engine: Engine
) -> None:
    """``total`` is the full RLS-VISIBLE count, not the firm's: two clients of the same firm
    paging the same endpoint each get their own book size."""
    book = _mk_book(super_engine, 3)
    logins = _mk_sibling_client(super_engine, book)
    trust_wos, other_wos = str(logins["trust_wos"]), str(logins["solo_wos"])

    async with _authed(db["app"], trust_wos, [], role="client") as session:
        trust_page = await crm_router.list_accounts(limit=2, session=session)
    async with _authed(db["app"], other_wos, [], role="client") as session:
        other_page = await crm_router.list_accounts(limit=2, session=session)

    assert trust_page["total"] == 3 and len(trust_page["items"]) == 2
    assert other_page["total"] == 1 and len(other_page["items"]) == 1


@pytest.mark.asyncio
async def test_holdings_page_after_shaping_from_one_lake_read(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Holdings pagination cuts AFTER the current shaping: ``total`` is the full holdings count
    from the single lake read (never a second), the items are the same tagged dicts as the bare
    list's, and the paged order is pinned -- the lake's (account, value desc) leaves equal-value
    rows unordered, so the page seam adds ``asset_id`` as the tiebreak. The bare (no ``limit``)
    response stays the lake's own order, untouched."""
    book = _mk_book(super_engine, 2)
    a1, a2 = list(book["account_ids"])  # type: ignore[misc]
    exts: dict[int, str] = dict(book["exts"])  # type: ignore[arg-type]
    e1, e2 = sorted((exts[a1], exts[a2]))

    def h(ext: str, asset: str, value: float) -> dict[str, object]:
        return {"account_external_id": ext, "asset_id": asset, "asset_name": asset, "value": value}

    # Deliberately shuffled, with a value tie inside each account -- the case an ORDER BY on
    # (account, value) alone cannot pin.
    rows = [
        h(e2, "Z-tied", 70.0),
        h(e1, "B-tied", 100.0),
        h(e1, "M-solo", 50.0),
        h(e2, "A-tied", 70.0),
        h(e1, "A-tied", 100.0),
        h(e2, "K-top", 90.0),
        h(e2, "Q-solo", 10.0),
    ]
    reads: list[list[str]] = []

    async def fake_holdings(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        reads.append(sorted(ids))
        return [dict(r) for r in rows]

    monkeypatch.setattr(lake_holdings, "firm_holdings", fake_holdings)

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        bare = await crm_router.list_holdings(session=session)
        pages = [
            await crm_router.list_holdings(limit=3, offset=o, session=session) for o in (0, 3, 6)
        ]

    # Bare mode: the lake's order, untouched, tagged like today.
    assert isinstance(bare, list)
    assert [r["asset_id"] for r in bare] == [r["asset_id"] for r in rows]
    assert all(r["account_id"] in (a1, a2) and r["account_name"] for r in bare)

    # One lake read per request -- the total never costs a second one.
    assert len(reads) == 4  # bare + three pages
    assert all(p["total"] == len(rows) for p in pages)
    walked = [r["asset_id"] for p in pages for r in p["items"]]
    # (account, value desc, asset_id): the ties come back alphabetical, pages tile exactly.
    assert walked == ["A-tied", "B-tied", "M-solo", "K-top", "A-tied", "Z-tied", "Q-solo"]
    assert [len(p["items"]) for p in pages] == [3, 3, 1]
    # The paged items are the bare list's dicts (tags included), just reordered and sliced.
    assert {json.dumps(r, sort_keys=True) for r in bare} == {
        json.dumps(r, sort_keys=True) for p in pages for r in p["items"]
    }


# ---------------------------------------------------------------------------
# /crm/accounts + /crm/holdings search (q)
# ---------------------------------------------------------------------------


def _dress_book_for_search(super_engine: Engine, book: dict[str, object]) -> dict[str, list[int]]:
    """Names + a product for ``_mk_book``'s accounts so the accounts search has fields to bite
    on: the first half becomes 'Growth Portfolio <id>' (no product), the second half
    'Cash Reserve <id>' on a SIPP product -- so name matches and product-type matches are
    provably disjoint groups. Returns the account ids per group, in bare-list (a.id) order."""
    ids = [int(i) for i in book["account_ids"]]  # type: ignore[union-attr]
    half = len(ids) // 2
    growth, cash = ids[:half], ids[half:]
    with super_engine.connect() as c:
        # The registry row still has to exist — `sync_platforms` seeds it on startup and the
        # merge resolves `platform.platforms.provider` — but the product no longer hangs off it
        # (0130 dropped `products.platform_id`), so the id is not bound to anything.
        c.execute(text(SECCL_PLATFORM_SQL))
        product = c.execute(
            text(
                "INSERT INTO platform.products (product_type, name) "
                "VALUES ('SIPP', 'Scale Pension') RETURNING id"
            ),
            {},
        ).scalar_one()
        for aid in growth:
            c.execute(
                # `product_id = NULL` is stated, not assumed: `_mk_book` now puts a real shelf
                # item on three accounts in four, so "no product" has to be asked for if the
                # name-match and product-match groups are to stay disjoint.
                text(
                    "UPDATE platform.accounts "
                    "SET name = 'Growth Portfolio ' || id, product_id = NULL WHERE id = :a"
                ),
                {"a": aid},
            )
        for aid in cash:
            c.execute(
                text(
                    "UPDATE platform.accounts "
                    "SET name = 'Cash Reserve ' || id, product_id = :p WHERE id = :a"
                ),
                {"p": product, "a": aid},
            )
        c.commit()
    return {"growth": growth, "cash": cash}


@pytest.mark.asyncio
async def test_accounts_q_filters_name_and_product_type_and_pages_the_matches(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``q`` on /crm/accounts matches account name OR product type, case-insensitively; the
    paged ``total`` becomes the MATCH count (the unfiltered paged call still reports the whole
    book beside it) and ``offset`` walks the filtered list, so "showing 2 of 3 matches" and the
    next page's contents both come from the same filtered order. Bare mode honours it too."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 6)
    groups = _dress_book_for_search(super_engine, book)

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        bare = await crm_router.list_accounts(q="growth", session=session)
        upper = await crm_router.list_accounts(q="GROWTH", limit=2, session=session)
        page2 = await crm_router.list_accounts(q="GROWTH", limit=2, offset=2, session=session)
        by_product = await crm_router.list_accounts(q="sipp", session=session)
        unfiltered = await crm_router.list_accounts(limit=2, session=session)

    # Bare mode filters and stays a bare list, in the same (a.id) order as ever.
    assert isinstance(bare, list)
    assert [int(r["id"]) for r in bare] == groups["growth"]

    # Case-insensitive; total counts the MATCHES; offset walks the filtered list.
    assert upper["total"] == len(groups["growth"]) == 3
    assert [int(r["id"]) for r in upper["items"]] == groups["growth"][:2]
    assert [int(r["id"]) for r in page2["items"]] == groups["growth"][2:]
    assert page2["total"] == 3

    # Product type is a match field of its own -- these accounts' names carry no 'sipp'.
    assert [int(r["id"]) for r in by_product] == groups["cash"]

    # The unfiltered envelope still reports the whole visible book.
    assert unfiltered["total"] == 6


@pytest.mark.asyncio
async def test_holdings_q_filters_asset_name_and_isin_and_pages_the_matches(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``q`` on /crm/holdings matches asset name OR ISIN -- the exact two fields the legacy
    holdings page filtered client-side -- case-insensitively, on the shaped rows: the bare list
    keeps the lake's order minus the misses, and the paged ``total`` is the match count with
    ``offset`` walking the filtered (account, value desc, asset_id) order."""
    book = _mk_book(super_engine, 1)
    exts: dict[int, str] = dict(book["exts"])  # type: ignore[arg-type]
    ext = next(iter(exts.values()))

    def h(asset: str, isin: str | None, value: float) -> dict[str, object]:
        return {
            "account_external_id": ext,
            "asset_id": asset[:6],
            "asset_name": asset,
            "isin": isin,
            "value": value,
        }

    rows = [
        h("Fidelity Emerging Markets", "GB00FID0001", 400.0),
        h("Vanguard FTSE All-World", "IE00VAN0002", 300.0),
        h("iShares Emerging Markets", "IE00ISH0003", 200.0),
        h("Cash GBP", None, 100.0),
    ]

    async def fake_holdings(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        return [dict(r) for r in rows]

    monkeypatch.setattr(lake_holdings, "firm_holdings", fake_holdings)

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        bare = await crm_router.list_holdings(q="EMERGING", session=session)
        by_isin = await crm_router.list_holdings(q="ie00", session=session)
        trimmed = await crm_router.list_holdings(q="  emerging  ", session=session)
        pages = [
            await crm_router.list_holdings(q="emerging", limit=1, offset=o, session=session)
            for o in (0, 1, 2)
        ]
        unfiltered = await crm_router.list_holdings(limit=1, session=session)

    # Case-insensitive on the name, lake order preserved minus the misses; trimming is free.
    assert isinstance(bare, list)
    assert [r["asset_name"] for r in bare] == [
        "Fidelity Emerging Markets",
        "iShares Emerging Markets",
    ]
    assert bare == trimmed

    # ISIN is a match field of its own (the Cash row's None never blows the filter up).
    assert [r["isin"] for r in by_isin] == ["IE00VAN0002", "IE00ISH0003"]

    # Paged: total is the match count, offset walks the FILTERED (value desc) list, and the
    # unfiltered envelope beside it still counts the whole book.
    assert [p["total"] for p in pages] == [2, 2, 2]
    assert [r["asset_name"] for p in pages for r in p["items"]] == [
        "Fidelity Emerging Markets",
        "iShares Emerging Markets",
    ]
    assert pages[2]["items"] == []  # off the end of the matches: empty, not an error
    assert unfiltered["total"] == 4


@pytest.mark.asyncio
async def test_blank_q_is_a_no_op_and_an_oversized_q_truncates(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty or whitespace ``q`` must serialise byte-identically to no ``q`` at all -- an
    empty search box is not a filter -- in both bare and paged modes, on both listings. And a
    term beyond the 80-char cap is truncated, not rejected: the kept prefix still matches (the
    clamp philosophy of ``_page``, applied to text)."""
    book = _mk_book(super_engine, 3)
    exts: dict[int, str] = dict(book["exts"])  # type: ignore[arg-type]
    ext = next(iter(exts.values()))
    long_name = "A" * 80  # exactly the cap, so the truncated term equals the name

    async def fake_holdings(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        return [
            {
                "account_external_id": ext,
                "asset_id": "LONG",
                "asset_name": long_name,
                "isin": None,
                "value": 1.0,
            }
        ]

    monkeypatch.setattr(lake_holdings, "firm_holdings", fake_holdings)

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        accounts_bare = await crm_router.list_accounts(session=session)
        accounts_blank = await crm_router.list_accounts(q="   ", session=session)
        accounts_page = await crm_router.list_accounts(limit=2, session=session)
        accounts_page_blank = await crm_router.list_accounts(q="", limit=2, session=session)
        holdings_bare = await crm_router.list_holdings(session=session)
        holdings_blank = await crm_router.list_holdings(q=" \t ", session=session)
        oversized = await crm_router.list_holdings(q="a" * 80 + "zzz", session=session)

    assert json.dumps(accounts_blank, default=str) == json.dumps(accounts_bare, default=str)
    assert json.dumps(accounts_page_blank, default=str) == json.dumps(accounts_page, default=str)
    assert json.dumps(holdings_blank, default=str) == json.dumps(holdings_bare, default=str)
    assert accounts_page["total"] == 3 and isinstance(accounts_bare, list)

    # 83 chars in, 80 kept: the junk tail is dropped and the prefix still matches the name
    # case-insensitively. Without truncation this would match nothing.
    assert [r["asset_name"] for r in oversized] == [long_name]


@pytest.mark.asyncio
async def test_q_never_surfaces_a_sibling_clients_rows(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RLS bounds the search: on the two-client fixture, a client searching a term that
    provably matches the SIBLING's rows (their account name, their holding, their ISIN) gets
    zero matches -- ``q`` widens nothing, it only narrows the caller's own RLS-visible book --
    while the same term from the owning login finds them."""
    book = _mk_book(super_engine, 2)
    logins = _mk_sibling_client(super_engine, book)
    with super_engine.connect() as c:
        c.execute(
            text("UPDATE platform.accounts SET name = 'Trust Alpha ' || id WHERE contact_id = :c"),
            {"c": book["contact"]},
        )
        c.commit()

    exts: dict[int, str] = dict(book["exts"])  # type: ignore[arg-type]
    lake_rows: dict[str, list[dict[str, object]]] = {
        e: [
            {
                "account_external_id": e,
                "asset_id": f"TA{i}",
                "asset_name": "Trust Alpha Fund",
                "isin": "GB00TRUST111",
                "value": 100.0,
            }
        ]
        for i, e in enumerate(exts.values())
    }
    lake_rows[str(logins["solo_ext"])] = [
        {
            "account_external_id": str(logins["solo_ext"]),
            "asset_id": "SG1",
            "asset_name": "Solo Gem Fund",
            "isin": "GB00SOLO222",
            "value": 50.0,
        }
    ]

    async def fake_holdings(provider: str, uid: str, ids: list[str]) -> list[dict[str, object]]:
        # Honours the ids it is asked for, like the real adapter -- RLS decides what those are.
        return [dict(r) for i in ids for r in lake_rows.get(i, [])]

    monkeypatch.setattr(lake_holdings, "firm_holdings", fake_holdings)

    async with _authed(db["app"], str(logins["solo_wos"]), [], role="client") as session:
        foreign_accounts = await crm_router.list_accounts(
            q="Trust Alpha", limit=10, session=session
        )
        foreign_holdings = await crm_router.list_holdings(q="trust", limit=10, session=session)
        foreign_isin = await crm_router.list_holdings(q="GB00TRUST111", session=session)
        own = await crm_router.list_holdings(q="solo gem", session=session)

    assert foreign_accounts["items"] == [] and foreign_accounts["total"] == 0
    assert foreign_holdings == {"items": [], "total": 0}
    assert foreign_isin == []
    assert [r["asset_name"] for r in own] == ["Solo Gem Fund"]

    # The isolation is per-caller, not a global block on the term: the owning login finds them.
    async with _authed(db["app"], str(logins["trust_wos"]), [], role="client") as session:
        trust_holdings = await crm_router.list_holdings(q="TRUST", limit=10, session=session)
        trust_accounts = await crm_router.list_accounts(q="trust alpha", session=session)
        probe = await crm_router.list_accounts(q="secret", limit=10, session=session)

    assert trust_holdings["total"] == 2
    assert [int(r["id"]) for r in trust_accounts] == list(book["account_ids"])
    # The solo's 'Solo Secret Reserve' stays theirs.
    assert probe["items"] == [] and probe["total"] == 0


# ---------------------------------------------------------------------------
# SQL paging: contacts, transactions. limit=25 must not ship the book.
# ---------------------------------------------------------------------------


def _mk_contact_pages(super_engine: Engine, book: dict[str, object], n: int) -> None:
    """Extra individuals on ``book``'s tenant, so the contacts listing has more than a page."""
    tenant, s = int(book["tenant"]), str(book["suffix"])
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO crm.contacts "
                "  (tenant_id, uid, type, first_name, surname, service_type) "
                "SELECT :t, 'sclppl_' || :s || '_' || i, 'individual', 'Pat', "
                "       'Pager' || lpad(i::text, 3, '0'), 'advised' "
                "FROM generate_series(1, :n) AS g(i)"
            ),
            {"t": tenant, "s": s, "n": n},
        )
        c.execute(
            text(
                "INSERT INTO crm.contacts "
                "  (tenant_id, uid, type, first_name, surname, service_type) "
                "VALUES (:t, :uid, 'individual', 'Nia', 'Halloway', 'advised')"
            ),
            {"t": tenant, "uid": f"sclhal_{s}"},
        )
        c.commit()


@pytest.mark.asyncio
async def test_contacts_limit_does_not_return_the_book(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 25-row screen is a 25-row payload. Search still finds a surname that is not on page 1."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 1)
    _mk_contact_pages(super_engine, book, 40)

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        page = await crm_router.list_contacts(limit=25, session=session)
        found = await crm_router.list_contacts(q="Halloway", limit=25, session=session)
        closed = await crm_router.list_contacts(archived=True, limit=25, session=session)

    assert set(page) == _ENVELOPE_KEYS
    assert page["total"] == 42  # trust + 40 pagers + Halloway
    assert len(page["items"]) == 25
    assert [r["surname"] for r in found["items"]] == ["Halloway"]
    assert found["total"] == 1
    assert closed["total"] == 0


@pytest.mark.asyncio
async def test_list_search_boxes_match_the_way_the_search_bar_does(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The clients and accounts boxes read ``_search`` through ``listing.search_sql``: a typo
    finds the row from four characters, a fragment of an identifier finds it, a three-letter
    term does not fuzz onto unrelated words, and ``_search`` itself never reaches a response."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 6)
    _mk_contact_pages(super_engine, book, 3)
    groups = _dress_book_for_search(super_engine, book)

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        typo = await crm_router.list_contacts(q="Haloway", limit=25, session=session)
        by_uid = await crm_router.list_contacts(q="sclhal", limit=25, session=session)
        short = await crm_router.list_contacts(q="hal", limit=25, session=session)
        accounts_typo = await crm_router.list_accounts(q="growht", session=session)
        accounts_noise = await crm_router.list_accounts(q="sipp", session=session)

    assert [r["surname"] for r in typo["items"]] == ["Halloway"]
    assert [r["surname"] for r in by_uid["items"]] == ["Halloway"]
    # Three letters: containment only, which "Halloway" satisfies and nothing else does.
    assert [r["surname"] for r in short["items"]] == ["Halloway"]
    assert [int(r["id"]) for r in accounts_typo] == groups["growth"]
    # "sipp" is the product type, not a typo of the client's "Sizeable Trust".
    assert [int(r["id"]) for r in accounts_noise] == groups["cash"]
    for row in [*typo["items"], *accounts_typo]:
        assert "_search" not in row


@pytest.mark.asyncio
async def test_contact_status_facets_are_not_a_one_way_door(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Filtering Active must still offer Lead — otherwise the first pick is irreversible."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 2)
    _mk_contact_pages(super_engine, book, 3)

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        active = await crm_router.list_contacts(status="active", limit=25, session=session)
        leads = await crm_router.list_contacts(status="lead", limit=25, session=session)

    assert "lead" in active["facets"]["status"]
    assert "active" in leads["facets"]["status"]
    assert active["total"] >= 1
    assert leads["total"] >= 1


@pytest.mark.asyncio
async def test_transactions_paged_is_a_real_page_not_a_prefetch(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``paged=true`` counts the match set; ``limit=25`` returns 25 of 40, not a 2000-row
    pre-fetch. Bare-list (no ``paged``) stays a list."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 1)
    tenant, aid = int(book["tenant"]), int(book["account_ids"][0])
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO platform.transactions "
                "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                "   amount, currency, transaction_date) "
                "SELECT :t, :a, 'seccl', 'TXN_' || i, 'deposit', 10, 'GBP', CURRENT_DATE "
                "FROM generate_series(1, 40) AS g(i)"
            ),
            {"t": tenant, "a": aid},
        )
        c.commit()

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        page = await crm_router.list_transactions(paged=True, limit=25, session=session)
        page2 = await crm_router.list_transactions(paged=True, limit=25, offset=25, session=session)
        bare = await crm_router.list_transactions(limit=25, session=session)
        by_q = await crm_router.list_transactions(q="TXN_1", paged=True, session=session)

    assert set(page) == _ENVELOPE_KEYS | {"totals", "breakdown", "capped"}
    assert page["total"] == 40
    assert len(page["items"]) == 25
    assert [row["source_transaction_id"] for row in page["items"]] == [
        f"TXN_{i}" for i in range(40, 15, -1)
    ]
    assert len(page2["items"]) == 15
    assert page2["total"] == 40
    assert [row["source_transaction_id"] for row in page2["items"]] == [
        f"TXN_{i}" for i in range(15, 0, -1)
    ]
    assert isinstance(bare, list) and len(bare) == 25
    assert by_q["total"] >= 1


@pytest.mark.asyncio
async def test_transactions_needs_action_skips_settled_rows(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The client-record strip asks ``needs_action=true`` so it does not pull settled history."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 4)
    tenant = int(book["tenant"])
    with super_engine.connect() as c:
        sipp = int(
            c.execute(
                text(
                    "SELECT a.id FROM platform.accounts a "
                    "JOIN platform.products p ON p.id = a.product_id "
                    "WHERE a.contact_id = :c AND p.product_type::text = 'SIPP' "
                    "ORDER BY a.id LIMIT 1"
                ),
                {"c": int(book["contact"])},
            ).scalar_one()
        )
        gia = int(
            c.execute(
                text(
                    "SELECT a.id FROM platform.accounts a "
                    "LEFT JOIN platform.products p ON p.id = a.product_id "
                    "WHERE a.contact_id = :c AND coalesce(p.product_type::text, '') <> 'SIPP' "
                    "ORDER BY a.id LIMIT 1"
                ),
                {"c": int(book["contact"])},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO platform.transactions "
                "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                "   amount, currency, transaction_date) "
                "VALUES (:t, :sipp, 'seccl', 'NEED', 'deposit', 50, 'GBP', CURRENT_DATE), "
                "       (:t, :gia, 'seccl', 'SETTLED', 'deposit', 50, 'GBP', CURRENT_DATE)"
            ),
            {"t": tenant, "sipp": sipp, "gia": gia},
        )
        c.commit()

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        strip = await crm_router.list_transactions(
            needs_action=True, paged=True, limit=20, session=session
        )
        alias = await crm_router.list_transactions(
            needs_categorisation=True, paged=True, limit=20, session=session
        )
        all_rows = await crm_router.list_transactions(paged=True, limit=20, session=session)

    assert strip["total"] == 1
    assert [r["uid"] for r in strip["items"]] == ["seccl:NEED"]
    assert alias["total"] == 1
    assert all_rows["total"] == 2


# ---------------------------------------------------------------------------
# Ranges, facet counts and bounds -- the rest of the filter panel's contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accounts_value_range_narrows_and_reports_the_books_ends(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``?value=&value=3000`` is "up to 3,000" and must RETURN only those rows -- the range was
    accepted and changed nothing, so the chip said it was applied and the table disagreed. Both
    ends are inclusive, and ``bounds`` stay the whole book's ends while a range is applied, so
    the slider's track never moves under the handle being dragged."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 6)  # values 1000..6000, in id order

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        whole = await crm_router.list_accounts(limit=25, session=session)
        upto = await crm_router.list_accounts(value=["", "3000"], limit=25, session=session)
        band = await crm_router.list_accounts(value=["2000", "4000"], limit=25, session=session)
        bare = await crm_router.list_accounts(value=["5000", ""], session=session)

    assert whole["total"] == 6
    assert whole["bounds"] == {"value": [1000.0, 6000.0]}
    assert upto["total"] == 3
    assert [r["value"] for r in upto["items"]] == [1000.0, 2000.0, 3000.0]
    assert band["total"] == 3  # 2000 and 4000 are both IN the band
    assert band["bounds"] == {"value": [1000.0, 6000.0]}
    # Bare mode filters too, or the same URL means two different things with and without a limit.
    assert isinstance(bare, list) and [r["value"] for r in bare] == [5000.0, 6000.0]


@pytest.mark.asyncio
async def test_account_facet_counts_answer_what_ticking_the_option_would_give(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each option's tally is taken over the rows its own option list is taken over -- this
    facet's filter lifted, every other one applied. A count taken over the visible rows instead
    would just be the number already on screen, and reads as a promise the table then breaks."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 6)  # product types cycle SIPP, GIA, (none), ISA, SIPP, GIA

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        whole = await crm_router.list_accounts(limit=25, session=session)
        sipp = await crm_router.list_accounts(type="SIPP", limit=25, session=session)

    assert whole["facet_counts"]["type"] == {"SIPP": 2, "GIA": 2, "ISA": 1}
    assert whole["facets"]["type"] == sorted(whole["facet_counts"]["type"], key=str.casefold)
    # Type keeps its OWN filter lifted, so picking SIPP leaves the other wrappers offered with
    # the count they would return -- not collapsed to the two rows now on screen.
    assert sipp["total"] == 2
    assert sipp["facet_counts"]["type"] == whole["facet_counts"]["type"]
    # A DIFFERENT facet is narrowed by the type filter, and counts only what is left.
    assert sipp["facet_counts"]["status"] == {"active": 2}
    assert whole["facet_counts"]["status"] == {"active": 6}


@pytest.mark.asyncio
async def test_contacts_value_range_leaves_the_unvalued_out(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client the book cannot value is UNKNOWN, not worth zero. ``portfolio_value`` is
    COALESCEd to 0 for the callers that sum it, so filtering on that column as-is would sweep
    the whole unpriced tail into "under £1,000" -- telling a firm it holds clients whose value
    it cannot actually see."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 3)  # one trust holding 1000 + 2000 + 3000
    _mk_contact_pages(super_engine, book, 2)  # two pagers + Halloway, no accounts at all
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO platform.accounts (tenant_id, contact_id, uid, status, value) "
                "SELECT :t, id, 'sclval_' || id, 'active', 500 FROM crm.contacts "
                " WHERE tenant_id = :t AND surname = 'Pager001'"
            ),
            {"t": int(book["tenant"])},
        )
        c.commit()

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        whole = await crm_router.list_contacts(limit=25, session=session)
        small = await crm_router.list_contacts(value=["", "1000"], limit=25, session=session)
        large = await crm_router.list_contacts(value=["1000", ""], limit=25, session=session)

    assert whole["total"] == 4  # the trust, two pagers, Halloway
    assert whole["bounds"] == {"value": [500.0, 6000.0]}
    assert whole["facet_counts"]["status"] == {"active": 2, "lead": 2}
    # One valued client each side, and the two unvalued in neither.
    assert small["total"] == 1
    assert [r["surname"] for r in small["items"]] == ["Pager001"]
    assert large["total"] == 1
    assert [r["portfolio_value"] for r in large["items"]] == [6000.0]


@pytest.mark.asyncio
async def test_transaction_amount_and_quantity_ranges_narrow_independently(
    db: dict[str, str], super_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two ranges on one listing. Each narrows on its own column and each reports its own ends
    with only its OWN filter lifted -- so a quantity filter shrinks the amount track (those are
    the rows still in play) while the quantity track it is being dragged along stays put. A cash
    movement carries no quantity, so it is outside every quantity range rather than in it at 0."""
    _fake_lake_empty(monkeypatch)
    book = _mk_book(super_engine, 1)
    tenant, aid = int(book["tenant"]), int(book["account_ids"][0])
    with super_engine.connect() as c:
        c.execute(
            # `source_transaction_id` is unique across the whole table, not per tenant, so the
            # fixture's ids carry this book's suffix.
            text(
                "INSERT INTO platform.transactions "
                "  (tenant_id, account_id, source_system, source_transaction_id, type, "
                "   amount, quantity, currency, transaction_date) "
                "SELECT :t, :a, 'seccl', 'RNG_' || :s || '_' || i, 'deposit', "
                "       (100 * i)::numeric, "
                "       CASE WHEN i <= 3 THEN (10 * i)::numeric END, 'GBP', CURRENT_DATE "
                "FROM generate_series(1, 5) AS g(i)"
            ),
            {"t": tenant, "a": aid, "s": str(book["suffix"])},
        )
        c.commit()

    async with _authed(db["app"], str(book["wos"]), ["contacts:read_all"]) as session:
        whole = await crm_router.list_transactions(paged=True, session=session)
        upto = await crm_router.list_transactions(paged=True, amount=["", "300"], session=session)
        qty = await crm_router.list_transactions(paged=True, quantity=["20", ""], session=session)
        # Rows 4 and 5 have an amount and no quantity. Amount's own filter is lifted, so its
        # track stays the whole column. The units track would otherwise disappear, so it falls
        # back to the book's own span.
        cash = await crm_router.list_transactions(paged=True, amount=["400", ""], session=session)

    assert whole["total"] == 5
    assert whole["bounds"] == {"amount": [100.0, 500.0], "quantity": [10.0, 30.0]}
    assert whole["facet_counts"]["currency"] == {"GBP": 5}  # the facet had no SQL at all
    assert upto["total"] == 3
    assert [r["amount"] for r in upto["items"]] == [300.0, 200.0, 100.0]  # date desc, id desc
    assert qty["total"] == 2  # the two rows with no quantity are unknown, not zero
    assert qty["bounds"]["quantity"] == [10.0, 30.0]
    assert qty["bounds"]["amount"] == [200.0, 300.0]
    assert cash["total"] == 2
    assert cash["bounds"]["amount"] == [100.0, 500.0]
    assert cash["bounds"]["quantity"] == [10.0, 30.0]
