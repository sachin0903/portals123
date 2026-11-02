"""Client classifications — match rules, resolve order, pin, and the public view.

A classification is a book label, not a WorkOS role. These tests pin the resolve
order the portal hook depends on: pinned → first matching rule by sort_order →
the tenant default. The advised-vs-D2C case is the reason the table exists.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import classifications as cl
from app.classifications import (
    ClassificationCapabilities,
    ClassificationFacts,
    ClassificationIn,
    ClassificationMatch,
)

Maker = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _Bed:
    def __init__(self, maker: Maker, nonce: str, ids: dict[str, Any]) -> None:
        self.maker = maker
        self.nonce = nonce
        self.tenant_id = int(ids["tenant_id"])
        self.contact_id = int(ids["contact_id"])
        self.eo_contact_id = int(ids["eo_contact_id"])


@pytest_asyncio.fixture
async def bed(maker: Maker) -> _Bed:
    nonce = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tenant_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"clf_{nonce}", "a": f"clf-{nonce}", "n": f"clf {nonce}"},
                )
            ).scalar_one()
        )

        async def contact(prefix: str, service: str) -> int:
            return int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO crm.contacts "
                            "(tenant_id, uid, name, type, service_type) "
                            "VALUES (:t, :u, :n, 'individual', :s) RETURNING id"
                        ),
                        {
                            "t": tenant_id,
                            "u": f"con_{prefix}_{nonce}",
                            "n": prefix,
                            "s": service,
                        },
                    )
                ).scalar_one()
            )

        contact_id = await contact("advised", "advised")
        eo_contact_id = await contact("d2c", "execution_only")
    return _Bed(
        maker,
        nonce,
        {
            "tenant_id": tenant_id,
            "contact_id": contact_id,
            "eo_contact_id": eo_contact_id,
        },
    )


def _match(**kwargs: Any) -> ClassificationMatch:
    return ClassificationMatch.model_validate(kwargs)


def test_slugify_is_url_safe() -> None:
    assert cl.slugify("Advised clients") == "advised-clients"
    assert cl.slugify("  D2C / Low-value  ") == "d2c-low-value"
    assert cl.slugify("???") == "classification"


def test_empty_rule_matches_everyone() -> None:
    facts = ClassificationFacts(contact_type="individual", account_service_types={"advised"})
    assert cl.match_rule(_match(), facts) is True


def test_contact_type_and_any_account_service() -> None:
    advised = ClassificationFacts(
        contact_type="individual", account_service_types={"advised", "discretionary"}
    )
    eo = ClassificationFacts(contact_type="individual", account_service_types={"execution_only"})
    rule = _match(any_account_service_types=["advised", "discretionary"])
    assert cl.match_rule(rule, advised) is True
    assert cl.match_rule(rule, eo) is False


def test_all_accounts_must_be_in_the_set() -> None:
    mixed = ClassificationFacts(account_service_types={"execution_only", "advised"})
    pure = ClassificationFacts(account_service_types={"execution_only"})
    rule = _match(account_service_types=["execution_only"])
    assert cl.match_rule(rule, mixed) is False
    assert cl.match_rule(rule, pure) is True


def test_no_accounts_falls_back_to_contact_service_type() -> None:
    rule = _match(account_service_types=["execution_only"])
    assert cl.match_rule(rule, ClassificationFacts(contact_service_type="execution_only")) is True
    assert cl.match_rule(rule, ClassificationFacts(contact_service_type="advised")) is False


def test_aum_bounds_refuse_a_missing_value() -> None:
    valued = ClassificationFacts(portfolio_value=4_000)
    unknown = ClassificationFacts(portfolio_value=None)
    rule = _match(max_value=10_000)
    assert cl.match_rule(rule, valued) is True
    assert cl.match_rule(rule, unknown) is False
    assert cl.match_rule(_match(min_value=5_000), valued) is False


def test_advisory_alias_normalises_on_the_rule() -> None:
    rule = ClassificationMatch.model_validate({"any_account_service_types": ["advisory"]})
    assert rule.any_account_service_types == ["advised"]


def test_public_view_omits_match_predicates() -> None:
    view = cl.public_view(
        {
            "uid": "abc",
            "name": "Advised",
            "slug": "advised",
            "match": {"any_account_service_types": ["advised"]},
            "capabilities": {"money_movement": False},
        },
        source="matched",
    )
    assert "match" not in view
    assert view["source"] == "matched"
    assert view["capabilities"] == {"money_movement": False}


async def _account(
    session: AsyncSession,
    *,
    tenant_id: int,
    contact_id: int,
    uid: str,
    capacity: str,
    value: float,
) -> None:
    await session.execute(
        text(
            "INSERT INTO platform.accounts "
            "(tenant_id, contact_id, uid, status, value, investment_capacity) "
            "VALUES (:t, :c, :u, 'active', :v, :cap)"
        ),
        {"t": tenant_id, "c": contact_id, "u": uid, "v": value, "cap": capacity},
    )


async def _catalogue(session: AsyncSession, tenant_id: int) -> dict[str, dict[str, Any]]:
    advised = await cl.create_classification(
        session,
        tenant_id,
        ClassificationIn(
            name="Advised",
            slug="advised",
            sort_order=0,
            match=ClassificationMatch(any_account_service_types=["advised", "discretionary"]),
        ),
    )
    d2c = await cl.create_classification(
        session,
        tenant_id,
        ClassificationIn(
            name="D2C",
            slug="d2c",
            sort_order=10,
            match=ClassificationMatch(account_service_types=["execution_only"], max_value=25_000),
            capabilities=ClassificationCapabilities(money_movement=True),
        ),
    )
    default = await cl.create_classification(
        session,
        tenant_id,
        ClassificationIn(name="Standard", slug="standard", sort_order=100, is_default=True),
    )
    return {"advised": advised, "d2c": d2c, "standard": default}


@pytest.mark.asyncio
async def test_empty_catalogue_resolves_to_nothing(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert await cl.resolve_for_contact(s, bed.tenant_id, bed.contact_id) is None


@pytest.mark.asyncio
async def test_advised_book_beats_d2c_and_the_default(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await _catalogue(s, bed.tenant_id)
        await _account(
            s,
            tenant_id=bed.tenant_id,
            contact_id=bed.contact_id,
            uid=f"acc_adv_{bed.nonce}",
            capacity="advisory",
            value=80_000,
        )
        resolved = await cl.resolve_for_contact(s, bed.tenant_id, bed.contact_id)
        assert resolved is not None
        row, source = resolved
        assert (row["slug"], source) == ("advised", "matched")


@pytest.mark.asyncio
async def test_low_value_execution_only_is_d2c(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await _catalogue(s, bed.tenant_id)
        await _account(
            s,
            tenant_id=bed.tenant_id,
            contact_id=bed.eo_contact_id,
            uid=f"acc_eo_{bed.nonce}",
            capacity="execution_only",
            value=4_200,
        )
        resolved = await cl.resolve_for_contact(s, bed.tenant_id, bed.eo_contact_id)
        assert resolved is not None
        row, source = resolved
        assert (row["slug"], source) == ("d2c", "matched")
        assert row["capabilities"]["money_movement"] is True


@pytest.mark.asyncio
async def test_high_value_execution_only_falls_to_the_default(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await _catalogue(s, bed.tenant_id)
        await _account(
            s,
            tenant_id=bed.tenant_id,
            contact_id=bed.eo_contact_id,
            uid=f"acc_hi_{bed.nonce}",
            capacity="execution_only",
            value=90_000,
        )
        resolved = await cl.resolve_for_contact(s, bed.tenant_id, bed.eo_contact_id)
        assert resolved is not None
        row, source = resolved
        assert (row["slug"], source) == ("standard", "default")


@pytest.mark.asyncio
async def test_pin_wins_and_clearing_it_rematches(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        rows = await _catalogue(s, bed.tenant_id)
        await _account(
            s,
            tenant_id=bed.tenant_id,
            contact_id=bed.contact_id,
            uid=f"acc_pin_{bed.nonce}",
            capacity="advisory",
            value=80_000,
        )
        pinned = await cl.pin_contact(s, bed.tenant_id, bed.contact_id, rows["d2c"]["uid"])
        assert pinned is not None
        assert pinned["slug"] == "d2c"
        assert pinned["source"] == "pinned"

        cleared = await cl.pin_contact(s, bed.tenant_id, bed.contact_id, None)
        assert cleared is not None
        assert cleared["slug"] == "advised"
        assert cleared["source"] == "matched"


@pytest.mark.asyncio
async def test_pin_of_an_unknown_uid_is_none(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert await cl.pin_contact(s, bed.tenant_id, bed.contact_id, "does-not-exist") is None


@pytest.mark.asyncio
async def test_creating_a_default_unsets_the_previous_one(bed: _Bed) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        first = await cl.create_classification(
            s, bed.tenant_id, ClassificationIn(name="A", slug="a", is_default=True)
        )
        second = await cl.create_classification(
            s, bed.tenant_id, ClassificationIn(name="B", slug="b", is_default=True)
        )
        listed = await cl.list_classifications(s, bed.tenant_id)
        defaults = [row["slug"] for row in listed if row["is_default"]]
        assert defaults == ["b"]
        refreshed = await cl.get_by_uid(s, bed.tenant_id, first["uid"])
        assert refreshed is not None and refreshed["is_default"] is False
        assert second["is_default"] is True
