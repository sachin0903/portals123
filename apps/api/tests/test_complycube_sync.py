"""The ComplyCube inbox and ``record_event``, against a real Postgres.

The receiver stores one ``compliance.events`` row per event id; the consumer fetches the check
from the provider and writes ``compliance.*``. These tests drive both halves with a fake provider.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.complycube import consumer
from app.complycube.sync import (
    MissingEventId,
    StatusChange,
    accept_event,
    build_plan,
    is_alert,
    matches_from_check,
    record_event,
    settle_status,
)
from app.config import settings
from tests.complycube_support import (
    Bed,
    FakeProvider,
    Maker,
    accept,
    as_json,
    drain,
    event,
    fetch,
    insert_check,
    screening_check,
    seed_bed,
)

MATCH_A = {
    "id": "m_a",
    "name": "Ada Lovelace",
    "matchTypes": ["pep_level_1"],
    "score": 92.5,
    "dob": "1815",
    "countries": ["GB"],
    "sources": [{"name": "UK PEP list"}],
}
MATCH_B = {"id": "m_b", "name": "A. Lovelace", "matchTypes": ["adverse_media"], "score": 61}


# ---- pure ---------------------------------------------------------------------------------------


def test_matches_are_read_from_the_check_breakdown() -> None:
    check = {"result": {"breakdown": {"matches": [MATCH_A, "not a match", MATCH_B]}}}
    parsed = matches_from_check(check)
    assert [m["provider_match_id"] for m in parsed] == ["m_a", "m_b"]
    first = parsed[0]
    assert first["name"] == "Ada Lovelace"
    assert first["match_types"] == ["pep_level_1"]
    assert first["score"] == Decimal("92.5")
    assert first["dob"] == "1815"
    assert first["countries"] == ["GB"]
    assert first["sources"] == ["UK PEP list"]
    assert first["raw"] == MATCH_A


def test_a_match_in_complycubes_documented_shape_is_read_from_its_details() -> None:
    """ComplyCube's AML screening check puts everything but the id and score under `details`."""
    documented = {
        "id": "m_doc",
        "score": 93,
        "details": {
            "entityName": "Susan Ann Lee Williams",
            "gender": "female",
            "countries": [{"type": "nationality", "country": "GB"}, {"country": "GB"}],
            "dates": [{"type": "Birth", "date": {"day": "4", "month": "3", "year": "1961"}}],
            "references": [{"source": "UK Sanctions List"}, {"source": "UK Sanctions List"}],
            "classifications": {"pep": [], "watchlist": ["UK HMT"], "adverseMedia": ["x"]},
        },
    }
    [parsed] = matches_from_check({"result": {"breakdown": {"matches": [documented]}}})
    assert parsed["name"] == "Susan Ann Lee Williams"
    assert parsed["match_types"] == ["watchlist", "adverse_media"]
    assert parsed["countries"] == ["GB"]
    assert parsed["dob"] == "1961-03-04"
    assert parsed["sources"] == ["UK Sanctions List"]
    assert parsed["score"] == Decimal("93")


def test_a_documented_match_name_falls_back_to_its_primary_name() -> None:
    item = {
        "id": "m_names",
        "details": {
            "names": [
                {"type": "alias", "firstName": "S", "lastName": "W"},
                {"type": "primary", "firstName": "Susan", "middleName": "Ann", "lastName": "Lee"},
            ]
        },
    }
    [parsed] = matches_from_check({"result": {"breakdown": {"matches": [item]}}})
    assert parsed["name"] == "Susan Ann Lee"


def test_a_match_without_an_id_gets_the_same_id_every_time() -> None:
    item = {"name": "No Id", "matchTypes": ["sanction"]}
    once = matches_from_check({"matches": [item]})[0]["provider_match_id"]
    again = matches_from_check({"matches": [dict(item)]})[0]["provider_match_id"]
    assert once == again
    assert once.startswith("h_")


def test_a_decision_stands_unless_the_provider_refers_again_with_something_new() -> None:
    # Undecided: the provider's status is taken as it is.
    assert settle_status(
        old_status="pending", decided=False, mapped="refer", new_matches=0, monitoring_event=False
    ) == ("refer", False)
    # Decided clear, the provider still says attention: the decision stands.
    assert settle_status(
        old_status="clear", decided=True, mapped="refer", new_matches=0, monitoring_event=False
    ) == ("clear", False)
    # A match we had not seen reopens it.
    assert settle_status(
        old_status="clear", decided=True, mapped="refer", new_matches=1, monitoring_event=False
    ) == ("refer", True)
    # So does a monitoring alert.
    assert settle_status(
        old_status="rejected", decided=True, mapped="refer", new_matches=0, monitoring_event=True
    ) == ("refer", True)
    # A provider status that is not a referral never overrides the decision.
    assert settle_status(
        old_status="rejected", decided=True, mapped="clear", new_matches=0, monitoring_event=False
    ) == ("rejected", False)


@pytest.mark.parametrize(
    ("old_status", "status", "reopened", "monitoring_event", "monitored", "alert"),
    [
        # A first verdict is never an alert, and neither is a check this event created.
        ("pending", "refer", False, False, False, False),
        (None, "refer", False, True, False, False),
        # A monitoring event referring a finished check.
        ("clear", "refer", True, True, False, True),
        # A monitored check that was decided, referred again.
        ("clear", "refer", True, False, True, True),
        ("rejected", "refer", False, False, True, True),
        # Unmonitored and no monitoring event: not an alert.
        ("clear", "refer", True, False, False, False),
        # A settled refer reopened stays refer: an alert only when it reopened.
        ("refer", "refer", True, True, False, True),
        ("refer", "refer", False, False, True, False),
        # Anything not landing on refer.
        ("refer", "clear", False, True, True, False),
    ],
)
def test_an_alert_is_a_finished_check_referred_again(
    old_status: str | None,
    status: str,
    reopened: bool,
    monitoring_event: bool,
    monitored: bool,
    alert: bool,
) -> None:
    assert (
        is_alert(
            old_status=old_status,
            status=status,
            reopened=reopened,
            monitoring_event=monitoring_event,
            monitored=monitored,
        )
        is alert
    )


async def test_build_plan_refuses_an_event_without_an_id() -> None:
    provider = FakeProvider()
    with pytest.raises(MissingEventId):
        await build_plan(provider.client(), {"type": "check.completed", "payload": {"id": "c"}})
    assert provider.calls == []


# ---- DB -----------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def bed(maker: Maker) -> AsyncIterator[Bed]:
    seeded = await seed_bed(maker)
    yield seeded
    await seeded.cleanup()


async def _check(bed: Bed, provider_check_id: str) -> dict[str, object]:
    rows = await fetch(
        bed.maker,
        "SELECT id, uid, status, outcome, provider_status, monitoring, decided_at, tenant_id "
        "FROM compliance.checks WHERE provider_check_id = :p",
        {"p": provider_check_id},
    )
    assert len(rows) == 1
    return rows[0]


async def _apply(bed: Bed, provider: FakeProvider, name: str, event_type: str, chk: str) -> None:
    assert await accept(bed.maker, event(bed, name, event_type, chk)) is not None
    result = await drain(bed.maker, provider)
    assert result is not None


async def _record(bed: Bed, provider: FakeProvider, payload: dict[str, object]) -> StatusChange:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        plan = await build_plan(provider.client(), payload)  # type: ignore[arg-type]
        return await record_event(s, plan)


async def test_check_completed_clears_a_pending_check(bed: Bed) -> None:
    chk = bed.ref("chk")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="clear")
    await _apply(bed, provider, "done", "check.completed", chk)
    row = await _check(bed, chk)
    assert row["status"] == "clear"
    assert row["outcome"] == "clear"
    inbox = await fetch(
        bed.maker,
        "SELECT processed_at, tenant_id, attempts FROM compliance.events "
        "WHERE provider_event_id = :e",
        {"e": f"evt_done_{bed.nonce}"},
    )
    assert inbox[0]["processed_at"] is not None
    assert inbox[0]["tenant_id"] == bed.tenant_id
    assert inbox[0]["attempts"] == 0


async def test_record_event_returns_the_old_and_new_status(bed: Bed) -> None:
    chk = bed.ref("chk")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="attention", matches=[MATCH_A])
    change = await _record(bed, provider, event(bed, "a", "check.completed.attention", chk))
    assert change == StatusChange("pending", "refer")
    again = await _record(bed, provider, event(bed, "b", "check.updated", chk))
    assert again == StatusChange("refer", "refer")


async def test_match_confirmed_refers_and_writes_the_matches(bed: Bed) -> None:
    chk = bed.ref("chk")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(
        bed, chk, outcome="match_confirmed", matches=[MATCH_A, MATCH_B]
    )
    await _apply(bed, provider, "mc", "check.completed.match_confirmed", chk)
    row = await _check(bed, chk)
    assert row["status"] == "refer"
    matches = await fetch(
        bed.maker,
        "SELECT provider_match_id, name, match_types, score, dob, countries, sources, raw, "
        "  decision, tenant_id "
        "FROM compliance.matches WHERE check_id = :c ORDER BY provider_match_id",
        {"c": row["id"]},
    )
    assert [m["provider_match_id"] for m in matches] == ["m_a", "m_b"]
    first = matches[0]
    assert first["name"] == "Ada Lovelace"
    assert first["match_types"] == ["pep_level_1"]
    assert first["score"] == Decimal("92.5")
    assert first["dob"] == "1815"
    assert first["countries"] == ["GB"]
    assert first["sources"] == ["UK PEP list"]
    assert as_json(first["raw"]) == MATCH_A
    assert first["decision"] is None
    assert first["tenant_id"] == bed.tenant_id


async def test_check_failed_is_a_technical_failure(bed: Bed) -> None:
    chk = bed.ref("chk")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, status="failed", outcome=None)
    await _apply(bed, provider, "f", "check.failed", chk)
    row = await _check(bed, chk)
    assert row["status"] == "failed"
    assert row["provider_status"] == "failed"


async def test_check_updated_records_what_the_provider_says_now(bed: Bed) -> None:
    chk = bed.ref("chk")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="rejected")
    # The webhook body claims clear; the fetch is the truth.
    payload = event(bed, "u", "check.updated", chk)
    payload["payload"] = {"id": chk, "status": "complete", "outcome": "clear"}
    await accept(bed.maker, payload)
    await drain(bed.maker, provider)
    assert (await _check(bed, chk))["status"] == "rejected"


async def test_monitoring_is_kept_in_step_with_the_provider(bed: Bed) -> None:
    chk = bed.ref("chk")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="clear", monitoring=True)
    await _apply(bed, provider, "m1", "check.completed", chk)
    assert (await _check(bed, chk))["monitoring"] is True
    provider.checks[chk] = screening_check(bed, chk, outcome="clear")
    await _apply(bed, provider, "m2", "check.updated", chk)
    # The provider said nothing about monitoring this time, so it stays on.
    assert (await _check(bed, chk))["monitoring"] is True


async def test_a_duplicate_event_id_is_stored_once_and_applied_once(bed: Bed) -> None:
    chk = bed.ref("chk")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="clear")
    payload = event(bed, "dup", "check.completed", chk)
    first = await accept(bed.maker, payload)
    second = await accept(bed.maker, payload)
    assert first is not None
    assert second is None
    rows = await fetch(
        bed.maker,
        "SELECT id FROM compliance.events WHERE provider_event_id = :e",
        {"e": payload["id"]},
    )
    assert [row["id"] for row in rows] == [first]
    await drain(bed.maker, provider)
    fetched = [call for call in provider.calls if call == f"GET /checks/{chk}"]
    assert len(fetched) == 1
    # A redelivery after it was applied is still not queued again.
    assert await accept(bed.maker, payload) is None


async def test_an_event_without_an_id_is_refused_and_not_stored(maker: Maker) -> None:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        before = (await s.execute(text("SELECT count(*) FROM compliance.events"))).scalar_one()
        for payload in (
            {"type": "check.completed", "payload": {"id": "x"}},
            {"id": "", "type": "check.completed"},
            {"id": "   ", "type": "check.completed"},
        ):
            with pytest.raises(MissingEventId):
                await accept_event(s, payload)
        after = (await s.execute(text("SELECT count(*) FROM compliance.events"))).scalar_one()
    assert after == before


async def test_events_applied_out_of_order_land_on_the_provider_state(bed: Bed) -> None:
    """A late ``check.pending`` after ``check.completed`` must not move the check back."""
    chk = bed.ref("chk")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="clear")
    completed = await _record(bed, provider, event(bed, "late2", "check.completed", chk))
    stale = await _record(bed, provider, event(bed, "late1", "check.pending", chk))
    assert completed == StatusChange("pending", "clear")
    assert stale == StatusChange("clear", "clear")
    assert (await _check(bed, chk))["status"] == "clear"


async def test_matches_are_upserted_and_a_reviewer_decision_survives_a_resync(bed: Bed) -> None:
    chk = bed.ref("chk")
    check_pk = await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="attention", matches=[MATCH_A])
    await _apply(bed, provider, "r1", "check.completed.attention", chk)
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "UPDATE compliance.matches SET decision = 'dismissed', decided_by = 'ops', "
                "  decided_at = now(), comment = 'Different person.' WHERE check_id = :c"
            ),
            {"c": check_pk},
        )
    changed = dict(MATCH_A, score=95)
    provider.checks[chk] = screening_check(bed, chk, outcome="attention", matches=[changed])
    await _apply(bed, provider, "r2", "check.updated", chk)
    rows = await fetch(
        bed.maker,
        "SELECT score, decision, comment FROM compliance.matches WHERE check_id = :c",
        {"c": check_pk},
    )
    assert rows == [
        {"score": Decimal("95"), "decision": "dismissed", "comment": "Different person."}
    ]


async def test_a_decided_check_keeps_its_decision_until_the_provider_finds_more(bed: Bed) -> None:
    chk = bed.ref("chk")
    check_pk = await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="attention", matches=[MATCH_A])
    await _apply(bed, provider, "d1", "check.completed.attention", chk)
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "UPDATE compliance.checks SET status = 'clear', decided_by = 'ops', "
                "  decided_at = now(), decision_comment = 'False positive.' WHERE id = :c"
            ),
            {"c": check_pk},
        )
    # The provider still reports attention with the same match: the decision stands.
    change = await _record(bed, provider, event(bed, "d2", "check.updated", chk))
    assert change == StatusChange("clear", "clear")
    assert (await _check(bed, chk))["decided_at"] is not None
    # A new match reopens the check and clears the decision.
    provider.checks[chk] = screening_check(
        bed, chk, outcome="attention", matches=[MATCH_A, MATCH_B]
    )
    reopened = await _record(bed, provider, event(bed, "d3", "check.updated", chk))
    assert reopened == StatusChange("clear", "refer")
    row = await _check(bed, chk)
    assert row["decided_at"] is None


async def _alerted_at(bed: Bed, provider_check_id: str) -> object:
    rows = await fetch(
        bed.maker,
        "SELECT alerted_at FROM compliance.checks WHERE provider_check_id = :p",
        {"p": provider_check_id},
    )
    return rows[0]["alerted_at"]


async def test_a_monitoring_alert_stamps_the_check(bed: Bed) -> None:
    chk = bed.ref("chk")
    check_pk = await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="attention", matches=[MATCH_A])
    # A first refer is not an alert.
    await _record(bed, provider, event(bed, "a1", "check.completed.attention", chk))
    assert await _alerted_at(bed, chk) is None
    # Settled: the match decided and the journey allowed, the check still `refer`.
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "UPDATE compliance.checks SET decided_by = 'ops', decided_at = now() WHERE id = :c"
            ),
            {"c": check_pk},
        )
    # The provider restating the same refer is not an alert either.
    await _record(bed, provider, event(bed, "a2", "check.updated", chk))
    assert await _alerted_at(bed, chk) is None
    # A monitoring alert reopens it, with no new match and no change of status, and stamps it.
    change = await _record(bed, provider, event(bed, "a3", "check.monitoring.attention", chk))
    assert change == StatusChange("refer", "refer")
    row = await _check(bed, chk)
    assert row["decided_at"] is None
    first = await _alerted_at(bed, chk)
    assert first is not None
    # A later event that is not an alert leaves the stamp as it was.
    await _record(bed, provider, event(bed, "a4", "check.updated", chk))
    assert await _alerted_at(bed, chk) == first


async def test_a_monitored_clear_check_referred_again_is_an_alert(bed: Bed) -> None:
    chk = bed.ref("chk")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="clear", monitoring=True)
    await _record(bed, provider, event(bed, "m1", "check.completed.clear", chk))
    assert await _alerted_at(bed, chk) is None
    provider.checks[chk] = screening_check(
        bed, chk, outcome="attention", monitoring=True, matches=[MATCH_A]
    )
    change = await _record(bed, provider, event(bed, "m2", "check.updated", chk))
    assert change == StatusChange("clear", "refer")
    assert await _alerted_at(bed, chk) is not None


def _stranger_check(provider: FakeProvider, chk: str, client: dict[str, object]) -> None:
    provider.clients[str(client["id"])] = client
    provider.checks[chk] = {
        "id": chk,
        "clientId": client["id"],
        "type": "extensive_screening_check",
        "status": "complete",
        "outcome": "attention",
        "result": {"breakdown": {"matches": [MATCH_A]}},
    }


async def _processed(bed: Bed, name: str) -> bool:
    inbox = await fetch(
        bed.maker,
        "SELECT processed_at FROM compliance.events WHERE provider_event_id = :e",
        {"e": f"evt_{name}_{bed.nonce}"},
    )
    return inbox[0]["processed_at"] is not None


async def test_a_client_this_database_does_not_hold_is_not_taken_in(bed: Bed) -> None:
    """One ComplyCube account serves several deployments, and every sandbox webhook sees every
    sandbox event. A client we do not hold is someone else's: nothing of it is stored, not even
    when its ``externalId`` is a contact uid we hold, or it carries our deployment's name."""
    for tag, extra in (
        ("bare", {}),
        ("named", {"metadata": {"graphene_env": settings.app_stage}}),
    ):
        chk = bed.ref(f"away{tag}")
        stranger = bed.ref(f"cli_away{tag}")
        provider = FakeProvider()
        _stranger_check(provider, chk, {"id": stranger, "externalId": bed.contact_uid, **extra})

        change = await _record(bed, provider, event(bed, f"away{tag}", "check.completed", chk))

        assert change == StatusChange(None, None)
        assert await _processed(bed, f"away{tag}")
        for table, column, value in (
            ("compliance.clients", "provider_client_id", stranger),
            ("compliance.checks", "provider_check_id", chk),
        ):
            rows = await fetch(
                bed.maker,
                f"SELECT 1 FROM {table} WHERE {column} = :v",  # noqa: S608 - fixed names
                {"v": value},
            )
            assert rows == []
        matches = await fetch(
            bed.maker,
            "SELECT 1 FROM compliance.matches m JOIN compliance.checks c ON c.id = m.check_id "
            "WHERE c.provider_check_id = :v",
            {"v": chk},
        )
        assert matches == []


async def test_a_held_client_complycube_files_under_another_contact_is_not_applied(
    bed: Bed,
) -> None:
    """Our ComplyCube client id with someone else's ``externalId``: the binding has gone wrong,
    or the id is another deployment's. The check keeps what it had; the event is done."""
    chk = bed.ref("crossed")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.clients[bed.provider_client_id]["externalId"] = "someone-else"
    provider.checks[chk] = screening_check(bed, chk, outcome="clear")

    change = await _record(bed, provider, event(bed, "crossed", "check.completed", chk))

    assert change == StatusChange(None, None)
    assert (await _check(bed, chk))["status"] == "pending"
    assert await _processed(bed, "crossed")
    (client,) = await fetch(
        bed.maker,
        "SELECT external_id FROM compliance.clients WHERE id = :c",
        {"c": bed.client_pk},
    )
    assert client["external_id"] != "someone-else"


async def test_a_held_client_whose_external_id_is_its_contact_is_applied(bed: Bed) -> None:
    """Both ids match: ours, stamped with a deployment name or not."""
    chk = bed.ref("matched")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.clients[bed.provider_client_id].pop("metadata", None)
    provider.checks[chk] = screening_check(bed, chk, outcome="clear")

    await _apply(bed, provider, "matched", "check.completed", chk)

    assert (await _check(bed, chk))["status"] == "clear"


async def test_an_identity_document_outside_the_nationalities_refers(bed: Bed) -> None:
    chk = bed.ref("idchk")
    doc = bed.ref("doc")
    photo = bed.ref("photo")
    provider = FakeProvider()
    provider.clients[bed.provider_client_id] = {
        "id": bed.provider_client_id,
        "externalId": bed.contact_uid,
        "entityName": "Ada Lovelace",
    }
    provider.documents[doc] = {
        "id": doc,
        "type": "passport",
        "classification": "proof_of_identity",
        "issuingCountry": "US",
        "documentNumber": "123456789",
    }
    provider.photos[photo] = {"id": photo, "performLivenessCheck": True, "size": 1200}
    provider.checks[chk] = {
        "id": chk,
        "clientId": bed.provider_client_id,
        "type": "identity_check",
        "status": "complete",
        "outcome": "clear",
        "documentId": doc,
        "livePhotoId": photo,
    }
    await insert_check(bed, chk, kind="identity")
    change = await _record(bed, provider, event(bed, "id", "check.completed.clear", chk))
    # The contact's nationality is GB; a US passport refers a clear identity check.
    assert change == StatusChange("pending", "refer")
    document = await fetch(
        bed.maker,
        "SELECT document_type, document_number, issuing_country, tenant_id "
        "FROM compliance.documents WHERE provider_document_id = :d",
        {"d": doc},
    )
    assert document == [
        {
            "document_type": "passport",
            "document_number": "123456789",
            "issuing_country": "US",
            "tenant_id": bed.tenant_id,
        }
    ]
    stored_photo = await fetch(
        bed.maker,
        "SELECT perform_liveness_check, size_bytes FROM compliance.live_photos "
        "WHERE provider_photo_id = :p",
        {"p": photo},
    )
    assert stored_photo == [{"perform_liveness_check": True, "size_bytes": 1200}]


# ---- the consumer -------------------------------------------------------------------------------


async def test_a_provider_outage_leaves_the_event_for_the_next_pass(bed: Bed) -> None:
    chk = bed.ref("chk")
    await insert_check(bed, chk)
    provider = FakeProvider(bed)
    provider.checks[chk] = screening_check(bed, chk, outcome="clear")
    provider.status_override = 503
    await accept(bed.maker, event(bed, "out", "check.completed", chk))
    result = await drain(bed.maker, provider)
    assert result is not None and result.deferred == 1
    inbox = await fetch(
        bed.maker,
        "SELECT processed_at, attempts FROM compliance.events WHERE provider_event_id = :e",
        {"e": f"evt_out_{bed.nonce}"},
    )
    # Not counted against the event: the provider was down, the event was fine.
    assert inbox == [{"processed_at": None, "attempts": 0}]
    provider.status_override = None
    await drain(bed.maker, provider)
    assert (await _check(bed, chk))["status"] == "clear"


async def test_a_permanent_error_is_counted_and_then_parked(bed: Bed) -> None:
    chk = bed.ref("gone")
    provider = FakeProvider()  # knows no check: every fetch is a 404
    await accept(bed.maker, event(bed, "gone", "check.completed", chk))
    for _ in range(consumer.MAX_ATTEMPTS + 2):
        await drain(bed.maker, provider)
    inbox = await fetch(
        bed.maker,
        "SELECT processed_at, attempts, last_error FROM compliance.events "
        "WHERE provider_event_id = :e",
        {"e": f"evt_gone_{bed.nonce}"},
    )
    assert inbox[0]["processed_at"] is None
    assert inbox[0]["attempts"] == consumer.MAX_ATTEMPTS
    assert "404" in str(inbox[0]["last_error"])
    fetched = [call for call in provider.calls if call == f"GET /checks/{chk}"]
    assert len(fetched) == consumer.MAX_ATTEMPTS


async def test_one_bad_event_does_not_undo_the_rest_of_the_batch(bed: Bed) -> None:
    good = bed.ref("good")
    await insert_check(bed, good)
    provider = FakeProvider(bed)
    provider.checks[good] = screening_check(bed, good, outcome="clear")
    await accept(bed.maker, event(bed, "bad", "check.completed", bed.ref("missing")))
    await accept(bed.maker, event(bed, "good", "check.completed", good))
    await drain(bed.maker, provider)
    assert (await _check(bed, good))["status"] == "clear"


async def test_a_second_consumer_skips_while_the_first_holds_the_lock(bed: Bed) -> None:
    provider = FakeProvider()
    async with bed.maker() as holder, holder.begin():
        await holder.execute(text("SET LOCAL ROLE service_role"))
        assert await consumer.claim_consumer(holder)
        assert await drain(bed.maker, provider) is None
    result = await drain(bed.maker, provider)
    assert result is not None
