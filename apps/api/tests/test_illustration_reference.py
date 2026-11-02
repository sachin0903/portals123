"""The admin illustration-reference reads. Pins the optional-filter query running with no filter —
`:isin IS NULL` on a bare parameter is AmbiguousParameter in Postgres unless the param is cast."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers import illustration_reference

pytestmark = pytest.mark.asyncio


async def test_list_instrument_costs_with_no_isin_does_not_raise(
    service_session: AsyncSession,
) -> None:
    # isin=None is the default (no filter); the query must run, not 500 on AmbiguousParameter.
    rows = await illustration_reference.list_instrument_costs(
        isin=None, _=None, session=service_session
    )
    assert isinstance(rows, list)


async def test_list_instrument_costs_filters_by_isin(service_session: AsyncSession) -> None:
    rows = await illustration_reference.list_instrument_costs(
        isin="GB00NONEXISTENT", _=None, session=service_session
    )
    assert rows == []
