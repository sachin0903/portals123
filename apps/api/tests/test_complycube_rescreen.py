"""Re-screen on changed details (0223, 0253), against a real Postgres.

A change to a client's name, date of birth or nationality marks their latest portal AML check
stale when it has a result — clear, refer or rejected — expires the pass it gave, and queues one
``aml.rescreen.requested`` outbox row. It is a trigger on ``crm.contacts``, so each write path is
driven here through its own code: the client record's PATCH, a flow's form write-back, the partner
API's client PATCH, and a direct write as the provider pipelines and imports make one.

The row screens the client again by itself, on their new details, after a clear screen or one
allowed past its matches, and asks operations after a rejected screen or one under review.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.complycube import rescreen as rescreen_module
from app.complycube import tasks
from app.complycube.client import ComplyCubeNotConfigured
from app.complycube.rescreen import EVENT, rescreen, rescreen_task_ref
from app.flows.dispatcher import PermanentDispatchError
from tests.complycube_support import (
    Bed,
    FakeProvider,
    Maker,
    as_json,
    fetch,
    insert_check,
    make_tenant,
    seed_bed,
)

DOB = date(1815, 12, 10)


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def bed(maker: Maker) -> AsyncIterator[Bed]:
    """A firm's client with every screened detail set, and no check yet."""
    seeded = await seed_bed(maker)
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "UPDATE crm.contacts SET first_name = 'Ada', surname = 'Lovelace', dob = :dob, "
                "  email = :email WHERE id = :c"
            ),
            {"dob": DOB, "email": f"ada{seeded.nonce}@example.test", "c": seeded.contact_id},
        )
    yield seeded
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("DELETE FROM flows.outbox WHERE tenant_id = ANY(:ids)"),
            {"ids": [seeded.tenant_id, *seeded.extra_tenants]},
        )
    await seeded.cleanup()


@pytest_asyncio.fixture
async def ops(bed: Bed, monkeypatch: pytest.MonkeyPatch) -> int:
    """Graphene's operations tenant with its compliance role, for the task paths."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        ops_id = await make_tenant(s, "cco", bed.nonce)
        await s.execute(
            text(
                "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                "VALUES (:t, :s, 'Compliance', 'operations')"
            ),
            {"t": ops_id, "s": tasks.COMPLIANCE_ROLE_SLUG},
        )
    bed.extra_tenants.append(ops_id)

    async def fake_ops_tenant(session: AsyncSession) -> int:
        del session
        return ops_id

    monkeypatch.setattr(tasks, "operations_tenant_id", fake_ops_tenant)
    return ops_id


# ---- helpers ------------------------------------------------------------------------------------


async def _clear_portal_check(bed: Bed, name: str) -> tuple[int, str]:
    pk = await insert_check(bed, bed.ref(name), origin="portal", status="clear")
    return pk, await _uid(bed, pk)


async def _portal_check(
    bed: Bed, name: str, status: str, *, decided: bool = False, passed: bool = False
) -> tuple[int, str, int | None]:
    """A portal AML check with ``status``: decided when asked, and with a passed kyc row when
    asked (as a clear or allowed screen leaves one)."""
    kyc: int | None = None
    if passed:
        async with bed.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            kyc = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO crm.kyc_checks (tenant_id, contact_id, kind, status, "
                            "  provider, verified_at) "
                            "VALUES (:t, :c, 'aml', 'passed', 'complycube', now()) RETURNING id"
                        ),
                        {"t": bed.tenant_id, "c": bed.contact_id},
                    )
                ).scalar_one()
            )
    pk = await insert_check(bed, bed.ref(name), origin="portal", status=status, kyc_check_id=kyc)
    if decided:
        async with bed.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            await s.execute(
                text(
                    "UPDATE compliance.checks SET decided_by = 'Rita Reviewer', "
                    "  decided_at = now() WHERE id = :c"
                ),
                {"c": pk},
            )
    return pk, await _uid(bed, pk), kyc


async def _kyc_status(bed: Bed, kyc_pk: int) -> str:
    rows = await fetch(bed.maker, "SELECT status FROM crm.kyc_checks WHERE id = :k", {"k": kyc_pk})
    return str(rows[0]["status"])


async def _aml_checks(bed: Bed) -> list[dict[str, Any]]:
    return await fetch(
        bed.maker,
        "SELECT id, status, provider_check_id, kyc_check_id FROM compliance.checks "
        "WHERE client_id = :c AND kind = 'aml' ORDER BY id",
        {"c": bed.client_pk},
    )


async def _uid(bed: Bed, check_pk: int) -> str:
    rows = await fetch(
        bed.maker, "SELECT uid FROM compliance.checks WHERE id = :c", {"c": check_pk}
    )
    return str(rows[0]["uid"])


async def _stale_at(bed: Bed, check_pk: int) -> Any:
    rows = await fetch(
        bed.maker, "SELECT stale_at FROM compliance.checks WHERE id = :c", {"c": check_pk}
    )
    return rows[0]["stale_at"]


async def _outbox(bed: Bed) -> list[dict[str, Any]]:
    rows = await fetch(
        bed.maker,
        "SELECT tenant_id, event_type, payload, status FROM flows.outbox "
        "WHERE tenant_id = :t ORDER BY id",
        {"t": bed.tenant_id},
    )
    return [{**row, "payload": as_json(row["payload"])} for row in rows]


def _queued(bed: Bed, check_uid: str) -> dict[str, Any]:
    return {
        "tenant_id": bed.tenant_id,
        "event_type": EVENT,
        "payload": {"contact_id": bed.contact_id, "check_uid": check_uid},
        "status": "queued",
    }


async def _write(bed: Bed, set_sql: str, params: dict[str, Any] | None = None) -> None:
    """A direct write as service_role, the way an import or a backfill makes one."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(f"UPDATE crm.contacts SET {set_sql} WHERE id = :c"),  # noqa: S608 - test-only literal
            {**(params or {}), "c": bed.contact_id},
        )


async def _assert_marked_once(bed: Bed, check_pk: int, check_uid: str) -> None:
    assert await _stale_at(bed, check_pk) is not None
    assert await _outbox(bed) == [_queued(bed, check_uid)]


async def _assert_untouched(bed: Bed, check_pk: int) -> None:
    assert await _stale_at(bed, check_pk) is None
    assert await _outbox(bed) == []


async def _ops_tasks(bed: Bed, ops_id: int) -> list[dict[str, Any]]:
    return await fetch(
        bed.maker,
        "SELECT title, description, status, source_kind, source_ref, priority FROM tasks.tasks "
        "WHERE tenant_id = :t ORDER BY id",
        {"t": ops_id},
    )


# ---- every write path ---------------------------------------------------------------------------


async def test_the_client_record_patch_marks_the_screen_stale(bed: Bed) -> None:
    """PATCH /firm/contacts/{uid}, driven through the route handler on a session built the way
    ``db.firm_session`` builds one: ``service_role`` with the caller's claims published.
    ``authenticated`` holds only SELECT on crm.contacts (0002), so no request writes it as that
    role."""
    from app.auth import Principal, build_jwt_claims
    from app.db import FirmContext
    from app.routers import firm as firm_router

    pk, uid = await _clear_portal_check(bed, "firm")
    principal = Principal(
        user_id="wos_staff", organisation_id="org_firm", permissions=["contacts:write"]
    )
    claims = {
        **build_jwt_claims(principal),
        "tenant_id": bed.tenant_id,
        "tenant_ids": [bed.tenant_id],
        "portal": "platform",
    }
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("SELECT set_config('request.jwt.claims', :claims, true)"),
            {"claims": json.dumps(claims)},
        )
        saved = await firm_router.update_contact(
            contact_uid=str(bed.contact_id),
            contact_id=bed.contact_id,
            body=firm_router.UpdateContact.model_validate({"surname": "King"}),
            ctx=FirmContext(session=s, tenant_id=bed.tenant_id, principal=principal),
        )
    assert saved["surname"] == "King"
    await _assert_marked_once(bed, pk, uid)


async def test_a_flow_form_write_back_marks_the_screen_stale(bed: Bed) -> None:
    """The fill_form step's write-back, on the engine's ``service_role`` session."""
    from app.flows.step_handlers import _write_contact_patch

    pk, uid = await _clear_portal_check(bed, "flow")
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await _write_contact_patch(s, bed.tenant_id, bed.contact_id, {"dob": "1815-12-11"})
    await _assert_marked_once(bed, pk, uid)


async def test_a_partner_api_patch_marks_the_screen_stale(bed: Bed) -> None:
    """``LiveStore.patch_client``, on a session built as ``external.db`` builds one. It rewrites
    every column, so an email-only patch must not count as a change of screened details."""
    from external.db import publish_tenant
    from external.live import LiveStore
    from external.models import ClientPatch

    pk, uid = await _clear_portal_check(bed, "partner")
    rows = await fetch(
        bed.maker, "SELECT uid FROM compliance.clients WHERE id = :c", {"c": bed.client_pk}
    )
    client_uid = str(rows[0]["uid"])

    async def patch(body: ClientPatch) -> None:
        async with bed.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            await publish_tenant(s, bed.tenant_id)
            assert await LiveStore(s).patch_client(client_uid, body, tenant_id=bed.tenant_id)

    await patch(ClientPatch(email=f"ada.king{bed.nonce}@example.test"))
    await _assert_untouched(bed, pk)
    await patch(ClientPatch(first_name="Augusta"))
    await _assert_marked_once(bed, pk, uid)


@pytest.mark.parametrize("role", ["service_role", "book_writer"])
async def test_a_direct_write_marks_the_screen_stale(
    bed: Bed, db: dict[str, str], role: str
) -> None:
    """An import writes as ``service_role``; the provider pipelines write as ``book_writer``, which
    holds no privilege on compliance.* or flows.*. The trigger reaches both tables for either."""
    pk, uid = await _clear_portal_check(bed, f"direct_{role}")
    engine = create_async_engine(db["super"])
    try:
        async with async_sessionmaker(engine)() as s, s.begin():
            await s.execute(text(f"SET LOCAL ROLE {role}"))
            await s.execute(
                text("UPDATE crm.contacts SET nationality = ARRAY['GB', 'IE'] WHERE id = :c"),
                {"c": bed.contact_id},
            )
    finally:
        await engine.dispose()
    await _assert_marked_once(bed, pk, uid)


# ---- which changes count ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("set_sql", "params"),
    [
        ("first_name = 'Augusta'", {}),
        ("surname = 'King'", {}),
        ("dob = :dob", {"dob": date(1815, 12, 11)}),
        ("nationality = ARRAY['FR']", {}),
        ("nationality = NULL", {}),
    ],
)
async def test_each_screened_detail_counts(bed: Bed, set_sql: str, params: dict[str, Any]) -> None:
    pk, uid = await _clear_portal_check(bed, "detail")
    await _write(bed, set_sql, params)
    await _assert_marked_once(bed, pk, uid)


async def test_a_new_name_counts_for_a_person_known_by_their_name_alone(bed: Bed) -> None:
    """Without both name parts, ``name`` is what ComplyCube is sent (``clients.person_payload``
    splits it), so changing it asks for a new screen."""
    await _write(bed, "first_name = NULL, surname = NULL")
    pk, uid = await _clear_portal_check(bed, "byname")
    await _write(bed, "name = 'Augusta Ada King'")
    await _assert_marked_once(bed, pk, uid)


async def test_a_new_name_for_a_person_with_both_parts_is_not_a_change(bed: Bed) -> None:
    """A person's ``name`` is their parts (0246): a write naming something else keeps the parts'
    name, so nothing ComplyCube is sent has changed."""
    pk, _ = await _clear_portal_check(bed, "byparts")
    await _write(bed, "name = 'Augusta Ada King'")
    await _assert_untouched(bed, pk)


async def test_rewriting_the_same_values_is_not_a_change(bed: Bed) -> None:
    pk, _ = await _clear_portal_check(bed, "same")
    await _write(
        bed,
        "name = 'Ada Lovelace', first_name = 'Ada', surname = 'Lovelace', dob = :dob, "
        "nationality = ARRAY['GB']",
        {"dob": DOB},
    )
    await _assert_untouched(bed, pk)


async def test_a_change_to_another_column_is_not_a_change(bed: Bed) -> None:
    pk, _ = await _clear_portal_check(bed, "other")
    await _write(bed, "email = :e, middle_name = 'Byron'", {"e": f"king{bed.nonce}@example.test"})
    await _assert_untouched(bed, pk)


# ---- which checks go stale ----------------------------------------------------------------------


@pytest.mark.parametrize("check_status", ["pending", "failed"])
async def test_a_screen_with_no_result_is_left_alone(bed: Bed, check_status: str) -> None:
    pk = await insert_check(bed, bed.ref(check_status), origin="portal", status=check_status)
    await _write(bed, "surname = 'King'")
    await _assert_untouched(bed, pk)


@pytest.mark.parametrize(
    ("check_status", "decided"), [("refer", False), ("refer", True), ("rejected", True)]
)
async def test_a_referred_or_rejected_screen_goes_stale_too(
    bed: Bed, check_status: str, decided: bool
) -> None:
    pk, uid, _ = await _portal_check(bed, check_status, check_status, decided=decided)
    await _write(bed, "surname = 'King'")
    await _assert_marked_once(bed, pk, uid)


async def test_the_pass_of_a_stale_screen_is_expired(bed: Bed) -> None:
    """A pass of someone else's details is never reused by a journey (``kyc.pick_valid``)."""
    _, _, kyc = await _portal_check(bed, "passed", "clear", passed=True)
    assert kyc is not None
    await _write(bed, "dob = :dob", {"dob": date(1816, 1, 1)})
    assert await _kyc_status(bed, kyc) == "expired"


async def test_a_partner_check_is_left_alone(bed: Bed) -> None:
    pk = await insert_check(bed, bed.ref("p"), origin="partner", status="clear")
    await _write(bed, "surname = 'King'")
    await _assert_untouched(bed, pk)


async def test_a_clear_check_a_later_aml_check_replaced_is_left_alone(bed: Bed) -> None:
    earlier, _ = await _clear_portal_check(bed, "earlier")
    later = await insert_check(bed, bed.ref("later"), origin="partner", status="pending")
    await _write(bed, "surname = 'King'")
    await _assert_untouched(bed, earlier)
    assert await _stale_at(bed, later) is None


async def test_a_later_identity_check_does_not_replace_the_aml_screen(bed: Bed) -> None:
    pk, uid = await _clear_portal_check(bed, "aml")
    await insert_check(bed, bed.ref("id"), kind="identity", origin="portal", status="clear")
    await _write(bed, "surname = 'King'")
    await _assert_marked_once(bed, pk, uid)


async def test_a_second_change_before_the_client_is_screened_again_queues_nothing(
    bed: Bed,
) -> None:
    pk, uid = await _clear_portal_check(bed, "twice")
    await _write(bed, "surname = 'King'")
    first = await _stale_at(bed, pk)
    await _write(bed, "first_name = 'Augusta'")
    assert await _stale_at(bed, pk) == first
    assert await _outbox(bed) == [_queued(bed, uid)]


# ---- screened again, or operations asked --------------------------------------------------------


async def _raise(bed: Bed, payload: dict[str, Any]) -> None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await rescreen(s, bed.tenant_id, payload)


@pytest.fixture
def provider(bed: Bed, monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    """ComplyCube, holding the bed's client."""
    fake = FakeProvider(bed)
    monkeypatch.setattr(rescreen_module, "require_client", fake.client)
    return fake


@pytest.mark.parametrize("allowed", [False, True], ids=["clear", "allowed past its matches"])
async def test_the_client_is_screened_again_on_their_new_details(
    bed: Bed, ops: int, provider: FakeProvider, allowed: bool
) -> None:
    old_pk, _, kyc = await _portal_check(
        bed, "old", "refer" if allowed else "clear", decided=allowed, passed=True
    )
    assert kyc is not None
    await _write(bed, "surname = 'King'")
    await _raise(bed, await _queued_payload(bed))

    # ComplyCube is told the new surname before the screen is paid for.
    paths = [path for path, _ in provider.writes]
    assert paths == [f"/clients/{bed.provider_client_id}", "/checks"]
    assert provider.writes[0][1]["personDetails"]["lastName"] == "King"
    assert "externalId" not in provider.writes[0][1]
    # A new screen, on a new pending kyc row; the old pass is expired; nobody is asked to act.
    checks = await _aml_checks(bed)
    assert [check["id"] for check in checks][0] == old_pk
    assert len(checks) == 2 and checks[1]["status"] == "pending"
    assert checks[1]["kyc_check_id"] not in (None, kyc)
    assert await _kyc_status(bed, kyc) == "expired"
    assert await _kyc_status(bed, int(checks[1]["kyc_check_id"])) == "pending"
    assert await _ops_tasks(bed, ops) == []
    # Our copy of the client carries the new surname.
    copy = await fetch(
        bed.maker, "SELECT last_name FROM compliance.clients WHERE id = :c", {"c": bed.client_pk}
    )
    assert copy[0]["last_name"] == "King"


@pytest.mark.parametrize(
    ("check_status", "decided", "words"),
    [("rejected", True, "rejected"), ("refer", False, "under review")],
)
async def test_a_rejected_or_reviewed_screen_is_not_screened_again_by_itself(
    bed: Bed, ops: int, provider: FakeProvider, check_status: str, decided: bool, words: str
) -> None:
    await _portal_check(bed, "held", check_status, decided=decided)
    await _write(bed, "surname = 'King'")
    await _raise(bed, await _queued_payload(bed))
    assert provider.writes == []
    raised = await _ops_tasks(bed, ops)
    # Named as the client record names them now.
    assert [task["title"] for task in raised] == ["Screen Ada King again"]
    assert words in raised[0]["description"]


async def test_a_screen_that_cannot_run_asks_operations(
    bed: Bed, ops: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unconfigured() -> Any:
        raise ComplyCubeNotConfigured("not configured")

    monkeypatch.setattr(rescreen_module, "require_client", unconfigured)
    _, uid = await _clear_portal_check(bed, "unrun")
    await _write(bed, "surname = 'King'")
    await _raise(bed, await _queued_payload(bed))
    raised = await _ops_tasks(bed, ops)
    assert [task["source_ref"] for task in raised] == [rescreen_task_ref(bed.contact_id, uid)]
    assert "could not run by itself (ComplyCube is not configured)" in raised[0]["description"]


async def _queued_payload(bed: Bed) -> dict[str, Any]:
    rows = await _outbox(bed)
    assert len(rows) == 1
    return dict(rows[0]["payload"])


async def test_the_row_raises_one_ops_task_that_screening_the_client_closes(
    bed: Bed, ops: int
) -> None:
    _, uid, _ = await _portal_check(bed, "task", "rejected", decided=True)
    await _write(bed, "surname = 'King'")
    payload = await _queued_payload(bed)
    await _raise(bed, payload)
    # Redelivered: the source ref is the idempotency key, so still one task.
    await _raise(bed, payload)
    raised = await _ops_tasks(bed, ops)
    assert len(raised) == 1
    # Named by the new surname, as the client record now names them, not the kickoff `name`.
    assert raised[0]["title"] == "Screen Ada King again"
    assert "date of birth" in raised[0]["description"]
    assert {key: raised[0][key] for key in ("status", "source_kind", "source_ref", "priority")} == {
        "status": "open",
        "source_kind": tasks.SOURCE_KIND,
        "source_ref": f"screen:{bed.contact_id}:na:stale:{uid}",
        "priority": "high",
    }
    assert raised[0]["source_ref"] == rescreen_task_ref(bed.contact_id, uid)
    # The firm's own queue holds nothing about the screen.
    firm_tasks = await fetch(
        bed.maker, "SELECT 1 FROM tasks.tasks WHERE tenant_id = :t", {"t": bed.tenant_id}
    )
    assert firm_tasks == []
    # POST /aml/screen closes `screen:{contact}:na` and every ref under it.
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert await tasks.close_screening_task(s, f"screen:{bed.contact_id}:na") == 1
    assert [row["status"] for row in await _ops_tasks(bed, ops)] == ["done"]


async def test_the_dispatcher_turns_the_queued_row_into_the_task(bed: Bed, ops: int) -> None:
    """End to end through ``dispatch_due``. One transaction, rolled back, with every other queued
    row pushed out of reach first, so no other test's rows are drained."""
    from app.flows.dispatcher import dispatch_due

    _, uid, _ = await _portal_check(bed, "e2e", "rejected", decided=True)
    async with bed.maker() as s:
        trans = await s.begin()
        try:
            await s.execute(text("SET LOCAL ROLE service_role"))
            await s.execute(
                text(
                    "UPDATE flows.outbox SET next_retry = now() + interval '1 day' "
                    "WHERE status = 'queued' AND tenant_id <> :t"
                ),
                {"t": bed.tenant_id},
            )
            await s.execute(
                text("UPDATE crm.contacts SET surname = 'King' WHERE id = :c"),
                {"c": bed.contact_id},
            )
            delivered = await dispatch_due(
                s, now=datetime.now(UTC) + timedelta(minutes=1), limit=50
            )
            row = (
                await s.execute(
                    text("SELECT status, last_error FROM flows.outbox WHERE tenant_id = :t"),
                    {"t": bed.tenant_id},
                )
            ).one()
            refs = (
                (
                    await s.execute(
                        text("SELECT source_ref FROM tasks.tasks WHERE tenant_id = :o"),
                        {"o": ops},
                    )
                )
                .scalars()
                .all()
            )
        finally:
            await trans.rollback()
    assert delivered == 1
    assert (row.status, row.last_error) == ("delivered", None)
    assert refs == [rescreen_task_ref(bed.contact_id, uid)]


async def test_a_later_stale_screen_raises_its_own_task(bed: Bed, ops: int) -> None:
    _, first_uid, _ = await _portal_check(bed, "first", "rejected", decided=True)
    await _write(bed, "surname = 'King'")
    await _raise(bed, await _queued_payload(bed))
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await tasks.close_screening_task(s, f"screen:{bed.contact_id}:na")
        await s.execute(text("DELETE FROM flows.outbox WHERE tenant_id = :t"), {"t": bed.tenant_id})
    # Screened again, rejected again, and then the details change again.
    _, second_uid, _ = await _portal_check(bed, "second", "rejected", decided=True)
    await _write(bed, "first_name = 'Augusta'")
    await _raise(bed, await _queued_payload(bed))
    refs = [(row["source_ref"], row["status"]) for row in await _ops_tasks(bed, ops)]
    assert refs == [
        (rescreen_task_ref(bed.contact_id, first_uid), "done"),
        (rescreen_task_ref(bed.contact_id, second_uid), "open"),
    ]


async def test_a_row_for_a_client_already_screened_again_raises_nothing(bed: Bed, ops: int) -> None:
    await _clear_portal_check(bed, "before")
    await _write(bed, "surname = 'King'")
    payload = await _queued_payload(bed)
    # The new screen lands before the dispatcher reaches the row.
    await insert_check(bed, bed.ref("after"), origin="portal", status="pending")
    await _raise(bed, payload)
    assert await _ops_tasks(bed, ops) == []


async def test_a_row_for_a_check_that_is_gone_raises_nothing(bed: Bed, ops: int) -> None:
    await _raise(bed, {"contact_id": bed.contact_id, "check_uid": "nosuchcheck0"})
    assert await _ops_tasks(bed, ops) == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"check_uid": "abc123def456"},
        {"contact_id": "7", "check_uid": "abc123def456"},
        {"contact_id": True, "check_uid": "abc123def456"},
        {"contact_id": 7},
        {"contact_id": 7, "check_uid": ""},
        {"contact_id": 7, "check_uid": 12},
    ],
)
async def test_a_malformed_row_is_a_permanent_failure(payload: dict[str, Any]) -> None:
    class Untouchable:
        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("a malformed row reached the database")

    session: Any = Untouchable()
    with pytest.raises(PermanentDispatchError):
        await rescreen(session, 1, payload)
