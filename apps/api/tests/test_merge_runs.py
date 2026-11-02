"""The merge run ledger — what the pipeline DID, not only what it refused.

`platform.merge_rejects` (0062) answers "what could not be written". It cannot answer the question
an operator actually opens the page with: **did the merge run at all?** A firm with no data, a firm
whose chain never ran, and a firm whose daemon has no `BOOK_WRITER_DSN` all present as an empty
book, and only the run ledger separates them.

`book_sink.merge.merge_tenant` has always built this ledger and returned it. It reached a warehouse
relation (`book_{uid}.merge_ledger`) that declares no strategy — so it held ONE run, and was
reachable only from the admin SQL console by someone who already knew to look for a relation named
after a tenant uid. 0076 gives it an OLTP home and this gives it a reader.

It also carries the counted OBSERVATIONS that are deliberately NOT rejects — "no allocations
published", "unmodelled model wrapper: SSAS" — which by design appear in no other table.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import platforms

PROVIDER = "seccl"


async def _firm(session: AsyncSession, name: str) -> int:
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name)"
                    " VALUES (encode(gen_random_bytes(8), 'hex'),"
                    "         'run-' || encode(gen_random_bytes(4), 'hex'), :n) RETURNING id"
                ),
                {"n": name},
            )
        ).scalar_one()
    )


async def _entry(
    session: AsyncSession,
    tenant_id: int,
    run_id: str,
    entity: str,
    *,
    created: int = 0,
    updated: int = 0,
    rejected: int = 0,
    reasons: dict[str, int] | None = None,
    note: str | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO platform.merge_runs"
            " (run_id, tenant_id, provider, entity, created, updated, rejected, reasons, note)"
            " VALUES (:run, :t, :p, :e, :c, :u, :r, CAST(:reasons AS jsonb), :note)"
        ),
        {
            "run": run_id,
            "t": tenant_id,
            "p": PROVIDER,
            "e": entity,
            "c": created,
            "u": updated,
            "r": rejected,
            "reasons": json.dumps(reasons or {}),
            "note": note,
        },
    )


@pytest.mark.asyncio
async def test_a_run_is_one_row_with_its_entities_nested(service_session: AsyncSession) -> None:
    """Grouped by run, not a flat list of entity rows: flat, "the last run wrote nothing" and
    "the last run did not happen" look the same again — which is the whole failure this fixes."""
    firm = await _firm(service_session, "Nested Firm")
    run = str(uuid.uuid4())
    await _entry(service_session, firm, run, "nodes", created=50)
    await _entry(service_session, firm, run, "accounts", created=376, updated=4)

    out = await platforms.merge_runs(service_session, PROVIDER, tenant_id=firm)
    assert len(out["runs"]) == 1
    only = out["runs"][0]
    assert only["created"] == 426
    assert only["updated"] == 4
    assert [e["entity"] for e in only["entities"]] == ["nodes", "accounts"]


@pytest.mark.asyncio
async def test_totals_are_the_sum_of_what_is_shown(service_session: AsyncSession) -> None:
    """A headline disagreeing with the rows beneath it sends someone looking for the difference —
    the same rule `merge_rejects` follows for its own total."""
    firm = await _firm(service_session, "Totals Firm")
    run = str(uuid.uuid4())
    await _entry(service_session, firm, run, "transactions", created=13_034, rejected=117)
    await _entry(service_session, firm, run, "instruments", created=133, rejected=2)

    only = (await platforms.merge_runs(service_session, PROVIDER, tenant_id=firm))["runs"][0]
    assert only["created"] == sum(e["created"] for e in only["entities"])
    assert only["rejected"] == sum(e["rejected"] for e in only["entities"]) == 119


@pytest.mark.asyncio
async def test_an_observation_survives_where_it_exists_nowhere_else(
    service_session: AsyncSession,
) -> None:
    """A counted observation is NOT a reject and never reaches `merge_rejects` — it does not stop
    the row. `reasons` is the only place "unmodelled model wrapper: SSAS" is ever recorded, and it
    is how we find out the product_type enum needs extending."""
    firm = await _firm(service_session, "Observing Firm")
    run = str(uuid.uuid4())
    await _entry(
        service_session,
        firm,
        run,
        "models",
        created=51,
        reasons={"no allocations published": 5, "unmodelled model wrapper: SSAS": 18},
    )

    entity = (await platforms.merge_runs(service_session, PROVIDER, tenant_id=firm))["runs"][0][
        "entities"
    ][0]
    assert entity["rejected"] == 0
    assert entity["reasons"]["unmodelled model wrapper: SSAS"] == 18


@pytest.mark.asyncio
async def test_a_failed_entity_is_counted_and_named(service_session: AsyncSession) -> None:
    """One entity raising does not stop the walk (the entities after it may not depend on it), so
    the run is PARTIAL rather than failed — and "15 merged, 1 failed" is the headline."""
    firm = await _firm(service_session, "Partial Firm")
    run = str(uuid.uuid4())
    await _entry(service_session, firm, run, "nodes", created=50)
    await _entry(service_session, firm, run, "accounts", note='column "key" does not exist')

    only = (await platforms.merge_runs(service_session, PROVIDER, tenant_id=firm))["runs"][0]
    assert only["failed"] == 1
    assert only["entities"][1]["note"]


@pytest.mark.asyncio
async def test_a_firms_runs_are_its_own(service_session: AsyncSession) -> None:
    ours = await _firm(service_session, "Ours")
    theirs = await _firm(service_session, "Theirs")
    await _entry(service_session, ours, str(uuid.uuid4()), "nodes", created=1)
    await _entry(service_session, theirs, str(uuid.uuid4()), "nodes", created=99)

    out = await platforms.merge_runs(service_session, PROVIDER, tenant_id=ours)
    assert len(out["runs"]) == 1
    assert out["runs"][0]["created"] == 1


@pytest.mark.asyncio
async def test_a_firm_that_never_merged_says_so_rather_than_erroring(
    service_session: AsyncSession,
) -> None:
    """The distinction the table exists for: no runs is a real, reportable answer — and it is a
    DIFFERENT answer from a run that wrote nothing."""
    firm = await _firm(service_session, "Silent Firm")
    assert (await platforms.merge_runs(service_session, PROVIDER, tenant_id=firm))["runs"] == []


# The append-only property is asserted by withheld PRIVILEGE, beside its sibling ledger's, in
# tests/test_pipeline_inbound.py::test_the_ledgers_are_append_only_even_for_service_role — the
# grants are what enforce it, so that is what the test reads.
