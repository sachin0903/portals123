"""The AML work queue (``GET /aml/work``, ``GET /aml/work/summary``), against a real Postgres.

Drives the router's own dependency and endpoint coroutines, as ``test_aml_router.py`` does, with
its fixtures and helpers. What is worth proving:

* the queue is exactly the clients the client list flags as needing attention, the longest wait
  first, each with its reasons, steps and age;
* ``reason`` keeps one ``attention.WORK_FILTERS`` key, ``?tenant=`` one firm, ``q`` one name, and
  a reason outside the closed map is ignored;
* the answer is cut at ``WORK_LIMIT`` and says so;
* the summary counts what the queue holds;
* a firm's own staff cannot read it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.complycube import attention
from app.routers import aml as aml_router
from tests import test_aml_router as router_tests
from tests.complycube_support import Bed, fetch

# The client page's fixtures, so a queue test stands on the same firm, operator and provider.
maker = router_tests.maker
console = router_tests.console
_bare_contact = router_tests._bare_contact
_check = router_tests._check
_contact = router_tests._contact
_ctx = router_tests._ctx
_ops = router_tests._ops
_principal = router_tests._principal


async def _work(console: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    tenant = kwargs.pop("tenant", console["tenant_uid"])
    async with _ctx(_ops(console, ["aml:read"]), tenant=tenant) as ctx:
        return await aml_router.list_work(ctx=ctx, **kwargs)


async def _summary(console: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    async with _ctx(_ops(console, ["aml:read"]), tenant=console["tenant_uid"]) as ctx:
        return await aml_router.work_summary(ctx=ctx, **kwargs)


async def _age(bed: Bed, check_uid: str, days: int) -> None:
    """Move a check, and every match on it, ``days`` into the past."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "UPDATE compliance.checks SET created_at = now() - make_interval(days => :d) "
                "WHERE uid = :u"
            ),
            {"u": check_uid, "d": days},
        )
        await s.execute(
            text(
                "UPDATE compliance.matches SET created_at = now() - make_interval(days => :d) "
                "WHERE check_id = (SELECT id FROM compliance.checks WHERE uid = :u)"
            ),
            {"u": check_uid, "d": days},
        )


async def _queue(console: dict[str, Any]) -> dict[str, str]:
    """Four clients that need a person, each waiting a different time, and two that do not.

    Returns each one's contact uid by tag.
    """
    bed = console["bed"]
    uids: dict[str, str] = {}
    _c, uids["hits"], hits_client = await _contact(bed, "hits")
    await _age(bed, await _check(bed, hits_client, "hits", matches=[None, None]), 12)
    _c, uids["ident"], ident_client = await _contact(bed, "ident")
    await _age(bed, await _check(bed, ident_client, "ident", kind="identity"), 1)
    _c, uids["held"], held_client = await _contact(bed, "held")
    await _age(bed, await _check(bed, held_client, "held", matches=["confirmed"]), 4)
    _c, uids["bare"] = await _bare_contact(bed, "bare")
    # Settled and clear: nobody needs to act.
    _c, uids["settled"], settled_client = await _contact(bed, "settled")
    await _check(bed, settled_client, "settled", decided=True, matches=["dismissed"])
    _c, uids["clear"], clear_client = await _contact(bed, "clear")
    await _check(bed, clear_client, "clear", status="clear")
    return uids


async def test_the_queue_is_every_client_that_needs_a_person_longest_wait_first(
    console: dict[str, Any],
) -> None:
    uids = await _queue(console)
    work = await _work(console)

    async with _ctx(_ops(console), tenant=console["tenant_uid"]) as ctx:
        listed = await aml_router.list_clients(ctx=ctx)
    flagged = {row["key"] for row in listed["items"] if row["needs_attention"]}
    assert {item["key"] for item in work["items"]} == flagged
    assert (work["total"], work["truncated"]) == (len(flagged), False)

    # The bare client fell due just now; the held check waits from its last decision, now too.
    keys = [item["key"] for item in work["items"]]
    assert keys[:2] == [uids["hits"], uids["ident"]]
    stamps = [item["waiting_since"] for item in work["items"]]
    assert stamps == sorted(stamps)

    hits = work["items"][0]
    assert hits["name"] == "Client hits"
    assert hits["type"] == "individual"
    assert hits["firm"] == {
        "uid": console["tenant_uid"],
        "name": hits["firm"]["name"],
        "icon_url": None,
    }
    assert hits["origin"] == "portal"
    assert [(r["code"], r["count"]) for r in hits["reasons"]] == [("open_hits", 2)]
    assert hits["age"]["band"] == "overdue"
    assert {s["key"]: s["state"] for s in hits["steps"]}["hits"] == "open"
    by_key = {item["key"]: item for item in work["items"]}
    assert [r["code"] for r in by_key[uids["held"]]["reasons"]] == ["held"]
    assert [r["code"] for r in by_key[uids["bare"]]["reasons"]] == ["screen_due"]
    assert by_key[uids["ident"]]["age"]["band"] in {"fresh", "ageing"}


async def test_the_reason_filter_keeps_one_kind_of_work(console: dict[str, Any]) -> None:
    uids = await _queue(console)
    assert [i["key"] for i in (await _work(console, reason="hits"))["items"]] == [uids["hits"]]
    assert [i["key"] for i in (await _work(console, reason="held"))["items"]] == [uids["held"]]
    assert [i["key"] for i in (await _work(console, reason="identity"))["items"]] == [uids["ident"]]
    screens = {i["key"] for i in (await _work(console, reason="screens"))["items"]}
    assert uids["bare"] in screens
    assert uids["hits"] not in screens
    assert (await _work(console, reason="monitoring"))["items"] == []
    # Outside the closed map: ignored, not an empty queue.
    assert (await _work(console, reason="'; DROP TABLE x"))["total"] == (await _work(console))[
        "total"
    ]


async def test_the_queue_narrows_to_a_firm_and_a_name(console: dict[str, Any]) -> None:
    uids = await _queue(console)
    ops_uid = (
        await fetch(
            console["bed"].maker,
            "SELECT uid FROM auth.tenants WHERE id = :t",
            {"t": console["ops"]},
        )
    )[0]["uid"]
    assert (await _work(console, tenant=ops_uid))["items"] == []
    named = await _work(console, q="client held")
    assert [item["key"] for item in named["items"]] == [uids["held"]]


async def test_a_long_queue_is_cut_and_says_so(
    console: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _queue(console)
    full = await _work(console)
    monkeypatch.setattr(aml_router, "WORK_LIMIT", 2)
    cut = await _work(console)
    assert [item["key"] for item in cut["items"]] == [item["key"] for item in full["items"]][:2]
    assert cut["total"] == full["total"]
    assert cut["truncated"] is True


async def test_the_summary_counts_the_queue(console: dict[str, Any]) -> None:
    await _queue(console)
    work = await _work(console)
    summary = await _summary(console)
    assert summary["total"] == work["total"]
    assert set(summary["by_reason"]) == set(attention.WORK_FILTERS)
    for key in attention.WORK_FILTERS:
        assert summary["by_reason"][key] == (await _work(console, reason=key))["total"], key
    assert summary["overdue"] == sum(
        1 for item in work["items"] if item["age"] and item["age"]["band"] == "overdue"
    )
    assert summary["overdue"] >= 1
    assert (await _summary(console, q="client held"))["total"] == 1


async def test_a_firm_principal_cannot_read_the_queue(console: dict[str, Any]) -> None:
    principal = _principal(
        console["firm_user"], ["contacts:read", "contacts:read_all", "screening:read"]
    )
    async with _ctx(principal) as ctx:
        with pytest.raises(HTTPException) as raised:
            await aml_router.list_work(ctx=ctx)
        assert raised.value.status_code == 403
        with pytest.raises(HTTPException) as raised:
            await aml_router.work_summary(ctx=ctx)
        assert raised.value.status_code == 403
