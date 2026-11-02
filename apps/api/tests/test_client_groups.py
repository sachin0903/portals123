"""Client groups — a catalogue of assigned tags, and replacing a contact's set.

A group is not a book and not a free-form automation tag. These tests pin the
replace: unknown uids change nothing, the read comes back in sort order, and
deleting a group drops the membership.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import client_groups as groups
from app.client_groups import GroupIn

Maker = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _Bed:
    def __init__(self, maker: Maker, tenant_id: int, contact_id: int) -> None:
        self.maker = maker
        self.tenant_id = tenant_id
        self.contact_id = contact_id


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
                    {"u": f"grp_{nonce}", "a": f"grp-{nonce}", "n": f"grp {nonce}"},
                )
            ).scalar_one()
        )
        contact_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, name, type) "
                        "VALUES (:t, :u, 'Ada', 'individual') RETURNING id"
                    ),
                    {"t": tenant_id, "u": f"con_{nonce}"},
                )
            ).scalar_one()
        )
    return _Bed(maker, tenant_id, contact_id)


def test_slugify_is_url_safe() -> None:
    assert groups.slugify("Workplace pensions") == "workplace-pensions"
    assert groups.slugify("???") == "group"


async def _create(bed: _Bed, name: str, *, sort_order: int = 0, slug: str | None = None) -> dict:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return await groups.create_group(
            s,
            bed.tenant_id,
            GroupIn(name=name, slug=slug, sort_order=sort_order),
        )


@pytest.mark.asyncio
async def test_replace_is_the_set_and_reads_in_sort_order(bed: _Bed) -> None:
    workplace = await _create(bed, "Workplace", sort_order=0, slug="workplace")
    charity = await _create(bed, "Charity", sort_order=10, slug="charity")
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        # Charity first in the request; the read still follows the catalogue.
        worn = await groups.set_contact_groups(
            s, bed.tenant_id, bed.contact_id, [charity["uid"], workplace["uid"], charity["uid"]]
        )
    assert [row["slug"] for row in worn] == ["workplace", "charity"]


@pytest.mark.asyncio
async def test_an_unknown_group_leaves_the_set_alone(bed: _Bed) -> None:
    workplace = await _create(bed, "Workplace", slug="workplace")
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await groups.set_contact_groups(s, bed.tenant_id, bed.contact_id, [workplace["uid"]])
        with pytest.raises(ValueError, match="Unknown"):
            await groups.set_contact_groups(s, bed.tenant_id, bed.contact_id, ["missing"])
        still = await groups.groups_for_contact(s, bed.tenant_id, bed.contact_id)
    assert [row["slug"] for row in still] == ["workplace"]


@pytest.mark.asyncio
async def test_clearing_and_deleting(bed: _Bed) -> None:
    workplace = await _create(bed, "Workplace", slug="workplace")
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await groups.set_contact_groups(s, bed.tenant_id, bed.contact_id, [workplace["uid"]])
        assert await groups.delete_group(s, bed.tenant_id, workplace["uid"]) is True
        assert await groups.groups_for_contact(s, bed.tenant_id, bed.contact_id) == []
        cleared = await groups.set_contact_groups(s, bed.tenant_id, bed.contact_id, [])
        assert cleared == []


@pytest.mark.asyncio
async def test_slug_is_unique_per_firm(bed: _Bed) -> None:
    await _create(bed, "Workplace", slug="workplace")
    with pytest.raises(IntegrityError):
        await _create(bed, "Workplace again", slug="workplace")


@pytest.mark.asyncio
async def test_public_view_omits_the_internal_id(bed: _Bed) -> None:
    row = await _create(bed, "Workplace", slug="workplace")
    view = groups.public_view(row)
    assert "id" not in view
    assert view["slug"] == "workplace"
    assert view["sort_order"] == 0
