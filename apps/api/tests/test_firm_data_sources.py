"""A firm's own view of the custodians feeding its book.

The firm had none. Whether its book was current — which custodians it is connected to, when data
last landed, what did not make it — was answerable only from Graphene's operations console, on
pages the firm cannot reach. Its advisers read a book with no way to tell "this client has no
transactions" from "nothing has landed since Tuesday".

Two properties are load-bearing and neither is visible from reading the endpoint alone:

* it is scoped SERVER-SIDE from the caller's verified identity, with no tenant parameter — the
  same posture as `/firm/audit/actions`, so there is no argument for a caller to get wrong;
* it withholds the SLICE KEYS. Those are the custodian's own identifiers for the firm's records
  and belong to an enablement only Graphene can set; `data_sources:read` has no write sibling, so
  putting them on a read-only page would only show a firm a control it cannot use.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import permissions, rbac
from app.routers import firm as firm_router


class _Principal:
    def __init__(self, permissions: list[str]) -> None:
        self.permissions = permissions
        self.is_system = False


class _Ctx:
    def __init__(self, session: AsyncSession, tenant_id: int, perms: list[str]) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self.principal = _Principal(perms)


async def _firm(session: AsyncSession, name: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name)"
                    " VALUES ('ten_' || encode(gen_random_bytes(8), 'hex'),"
                    "         'src-' || encode(gen_random_bytes(4), 'hex'), :n) RETURNING id"
                ),
                {"n": name},
            )
        ).scalar_one()
    )


async def _enable(
    session: AsyncSession, tenant_id: int, provider: str, *, enabled: bool = True
) -> None:
    await session.execute(
        text(
            "INSERT INTO platform.tenant_platforms (tenant_id, provider, config, enabled,"
            "                                       last_merged_at)"
            " VALUES (:t, :p, CAST(:c AS jsonb), :e, now())"
        ),
        {"t": tenant_id, "p": provider, "c": '{"node_ids": ["1"]}', "e": enabled},
    )


async def _run(session: AsyncSession, tenant_id: int, perms: list[str]) -> Any:
    return await firm_router.list_firm_data_sources(_Ctx(session, tenant_id, perms))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_it_lists_the_firms_enabled_custodians(service_session: AsyncSession) -> None:
    tenant = await _firm(service_session, "Connected Firm")
    await _enable(service_session, tenant, "seccl")

    sources = await _run(service_session, tenant, ["data_sources:read"])
    assert [s["provider"] for s in sources] == ["seccl"]
    assert sources[0]["name"] == "Seccl"
    assert sources[0]["last_merged_at"] is not None


@pytest.mark.asyncio
async def test_a_disabled_connection_is_not_a_data_source(service_session: AsyncSession) -> None:
    """It is also not this surface's business to explain why it is off — that is a conversation
    with Graphene, not a status line on the firm's own page."""
    tenant = await _firm(service_session, "Switched Off Firm")
    await _enable(service_session, tenant, "seccl", enabled=False)

    assert await _run(service_session, tenant, ["data_sources:read"]) == []


@pytest.mark.asyncio
async def test_the_slice_keys_are_withheld(service_session: AsyncSession) -> None:
    """The custodian's own identifiers for this firm's records. They belong to the enablement,
    which only Graphene can set — and `data_sources:read` has no write sibling, so showing them
    would only be showing a firm a control it cannot use."""
    tenant = await _firm(service_session, "Curious Firm")
    await _enable(service_session, tenant, "seccl")

    sources = await _run(service_session, tenant, ["data_sources:read"])
    assert "config" not in sources[0]
    assert "node_ids" not in str(sources[0])


@pytest.mark.asyncio
async def test_it_refuses_without_the_permission(service_session: AsyncSession) -> None:
    tenant = await _firm(service_session, "Unpermitted Firm")
    await _enable(service_session, tenant, "seccl")

    with pytest.raises(Exception, match="permission|403|Forbidden"):
        await _run(service_session, tenant, [])


def test_the_permission_is_platform_portal_and_read_only() -> None:
    """A firm may SEE its data path and not change it: which custodians feed it, and the slice of
    the warehouse that is theirs, stay Graphene's to set."""
    by_slug = {p.slug: p for p in permissions.ALL}
    assert by_slug["data_sources:read"].portal == "platform"
    assert "data_sources:write" not in by_slug
    # Not operations-only — it is grantable to a firm's own roles, unlike `aml:adjudicate`.
    assert "data_sources:read" not in rbac.OPERATIONS_ONLY
