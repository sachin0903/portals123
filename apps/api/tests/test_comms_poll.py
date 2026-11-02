"""The delivery FALLBACK — asking Resend what became of a message when it cannot tell us.

Against real Postgres, because every claim here is a claim about which rows the scan selects and
what the write does to them. The provider itself is faked at the HTTP boundary: what is under test
is our half of the exchange, not Resend's.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.comms import poll
from app.config import settings

pytestmark = pytest.mark.asyncio


class _FakeProvider:
    """Stands in for ``transport.fetch_last_event`` — the ONE place allowed to hold the Resend
    credential, which is why the fake sits at that seam rather than at httpx.

    Records every id it was asked about: a scan that quietly asked and discarded the answer would
    pass a status-only check while still spending the request.
    """

    def __init__(self, answers: dict[str, str | poll.transport.RetrievedEmailEvent]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    async def __call__(
        self, provider_message_ids: Any
    ) -> dict[str, poll.transport.RetrievedEmailEvent]:
        ids = list(provider_message_ids)
        self.asked.extend(ids)
        return {
            i: (
                answer
                if isinstance(answer, poll.transport.RetrievedEmailEvent)
                else poll.transport.RetrievedEmailEvent(event=answer)
            )
            for i in ids
            if (answer := self.answers.get(i)) is not None
        }


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> Any:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _tenant(maker: Any, tag: str) -> int:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return int(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"ten_{tag}", "a": f"poll-{tag}", "n": f"Poll {tag}"},
                )
            ).scalar_one()
        )


async def _message(maker: Any, tenant_id: int, *, uid: str, pmid: str | None, status: str) -> int:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return int(
            (
                await s.execute(
                    text(
                        "INSERT INTO comms.messages "
                        "  (uid, tenant_id, provider_message_id, recipient_kind, to_address, "
                        "   to_domain, subject, source, status, sent_at) "
                        "VALUES (:u, :t, :p, 'external', 'a@b.test', 'b.test', 'probe', 'app', "
                        "        :s, now()) RETURNING id"
                    ),
                    {"u": uid, "t": tenant_id, "p": pmid, "s": status},
                )
            ).scalar_one()
        )


async def _status(maker: Any, message_id: int) -> tuple[str, bool, int]:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            (
                await s.execute(
                    text(
                        "SELECT status, delivered_at IS NOT NULL AS delivered, "
                        "  (SELECT count(*) FROM comms.message_events e "
                        "   WHERE e.message_id = m.id) AS n "
                        "FROM comms.messages m WHERE id = :i"
                    ),
                    {"i": message_id},
                )
            )
            .mappings()
            .one()
        )
        return str(row["status"]), bool(row["delivered"]), int(row["n"])


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """The suite blanks the credential (conftest `_resend_unconfigured`); this path needs one to
    do anything at all, so it sets its own — a fake, and the transport is faked with it."""
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")


def _fake(
    answers: dict[str, str | poll.transport.RetrievedEmailEvent],
    monkeypatch: pytest.MonkeyPatch,
    *,
    listed: dict[str, str] | None = None,
    has_more: bool = False,
) -> _FakeProvider:
    """Fake BOTH provider reads.

    The list call is faked as UNAVAILABLE by default (`listed=None`), which puts these tests on the
    per-id fallback — the path they were written against. Without stubbing it at all they would make
    a real request to api.resend.com on every test, because `_configured` supplies a key: the call
    would 401, be swallowed, and the fallback would carry the test while the suite quietly talked to
    the internet.
    """
    provider = _FakeProvider(answers)
    monkeypatch.setattr(poll.transport, "fetch_last_events", provider)

    async def _list(*, limit: int = 100) -> tuple[dict[str, Any], bool] | None:
        if listed is None:
            return None
        return {
            mid: poll.transport.RetrievedEmailEvent(event=ev) for mid, ev in listed.items()
        }, has_more

    monkeypatch.setattr(poll.transport, "fetch_recent_events", _list)
    return provider


async def _stamp(maker: Any, message_id: int, column: str) -> datetime | None:
    """One timestamp column off a message. `column` is a literal from this file, never input."""
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return (
            await s.execute(
                text(f"SELECT {column} FROM comms.messages WHERE id = :i"),  # noqa: S608
                {"i": message_id},
            )
        ).scalar_one()


async def _sent_at(maker: Any, message_id: int) -> datetime | None:
    return await _stamp(maker, message_id, "sent_at")


async def _delivered_at(maker: Any, message_id: int) -> datetime | None:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return (
            await s.execute(
                text("SELECT delivered_at FROM comms.messages WHERE id = :i"),
                {"i": message_id},
            )
        ).scalar_one()


@pytest.fixture(autouse=True)
def _fresh_cursor() -> None:
    """The sweep position is module state, so a test must not inherit where the last one stopped."""
    poll.reset_cursor()


async def test_a_delivered_answer_advances_the_row(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: a message the webhook never heard about still reaches `delivered`."""
    tenant = await _tenant(maker, "adv")
    mid = await _message(maker, tenant, uid="pollmsg00001", pmid="re_adv", status="sent")
    _fake({"re_adv": "delivered"}, monkeypatch)

    assert await poll.refresh_pending(maker) >= 1
    assert await _status(maker, mid) == ("delivered", True, 1)


async def test_the_stamp_records_when_we_learned(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A polled delivery is stamped at the POLL, and after the send — never before it.

    The retrieval shape carries only the EMAIL's `created_at`, which `ledger.record` always
    precedes with `sent_at`. Stamping the delivery with it put `delivered_at` before `sent_at`, and
    the timeline sorts by instant, so the pane rendered "Delivered -> Sent". Supplying no timestamp
    lets `apply_provider_event`'s `now()` arm record the honest thing: when we found out.
    """
    tenant = await _tenant(maker, "when")
    mid = await _message(maker, tenant, uid="pollmsg0when", pmid="re_when", status="sent")
    _fake({"re_when": "delivered"}, monkeypatch)

    assert await poll.refresh_pending(maker) >= 1
    delivered_at = await _delivered_at(maker, mid)
    sent_at = await _sent_at(maker, mid)
    assert delivered_at is not None and sent_at is not None
    assert delivered_at >= sent_at, "a delivery must never be stamped before its own send"


async def test_polling_the_same_answer_twice_changes_nothing(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The synthetic `poll:{last_event}` id is what makes a repeat a no-op on the unique index.

    Without a stable id every pass would append another event row for the same fact, and the
    detail pane's repeat count — which exists to show a genuine redelivery — would climb for ever.
    """
    tenant = await _tenant(maker, "idem")
    mid = await _message(maker, tenant, uid="pollmsg00002", pmid="re_idem", status="sent")
    _fake({"re_idem": "delivered"}, monkeypatch)

    assert await poll.refresh_pending(maker) >= 1
    assert await _status(maker, mid) == ("delivered", True, 1)
    # The row is no longer pending, so the second sweep cannot touch it however it is ordered.
    poll.reset_cursor()
    await poll.refresh_pending(maker)
    assert await _status(maker, mid) == ("delivered", True, 1)


@pytest.mark.parametrize(
    ("status", "pmid", "tag"),
    [
        # A bounce has nowhere left to go and the rank would refuse the answer anyway.
        ("bounced", "re_term", "term"),
        # Nothing was ever posted, so there is no id to ask about and no opinion to get.
        ("suppressed", None, "nop"),
    ],
)
async def test_a_row_with_no_news_to_gain_is_never_asked_about(
    maker: Any, monkeypatch: pytest.MonkeyPatch, status: str, pmid: str | None, tag: str
) -> None:
    """Asking would spend a provider request to be told what we already hold."""
    tenant = await _tenant(maker, tag)
    await _message(maker, tenant, uid=f"pollmsg0{tag}", pmid=pmid, status=status)
    client = _fake({"re_term": "delivered"}, monkeypatch)

    await poll.refresh_pending(maker)
    # About THIS row, not "nothing at all": the sweep runs over the whole table as service_role, so
    # rows left by other tests are legitimately in the batch and an `== []` assertion would pass or
    # fail on file ordering rather than on the behaviour under test.
    assert not any(a.startswith(f"re_{tag}") or a == pmid for a in client.asked if a)


@pytest.mark.parametrize("last_event", ["opened", "clicked"])
async def test_a_read_receipt_answer_is_refused_here_too(
    maker: Any, monkeypatch: pytest.MonkeyPatch, last_event: str
) -> None:
    """The read-receipt rule is enforced on THIS path as well as at the webhook.

    A second door into the ledger is exactly how a rule ends up enforced in one place only — and
    Resend answers `last_event: "opened"` whenever open tracking is on, so this is reachable in
    practice rather than theoretically.
    """
    tenant = await _tenant(maker, f"rr{last_event[:3]}")
    # A DISTINCT provider id per parametrisation: two rows sharing one would be deduped into a
    # single target, so the second case would assert against the first case's row.
    pmid = f"re_rr_{last_event}"
    mid = await _message(maker, tenant, uid=f"pollmsg{last_event[:5]}", pmid=pmid, status="sent")
    _fake({pmid: last_event}, monkeypatch)

    await poll.refresh_pending(maker)
    assert await _status(maker, mid) == ("sent", False, 0)


async def test_a_404_is_an_ordinary_answer(maker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A message pruned at the provider, and one sent by another deployment sharing the account,
    both look exactly like this. Neither is an error, and neither may kill the pass."""
    tenant = await _tenant(maker, "gone")
    mid = await _message(maker, tenant, uid="pollmsg00005", pmid="re_gone", status="sent")
    _fake({}, monkeypatch)

    assert await poll.refresh_pending(maker) == 0
    assert await _status(maker, mid) == ("sent", False, 0)


async def test_nothing_happens_without_a_credential(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No key means no provider to ask. It returns rather than raising, so a deployment that mails
    nobody does not log an incident every pass."""
    monkeypatch.setattr(settings, "resend_api_key", "")
    tenant = await _tenant(maker, "unc")
    await _message(maker, tenant, uid="pollmsg00006", pmid="re_unc", status="sent")
    client = _fake({"re_unc": "delivered"}, monkeypatch)

    assert await poll.refresh_pending(maker) == 0
    assert client.asked == []  # not even a scan: no credential means no provider to ask


# ---------------------------------------------------------------------------------------------
# One request, not one per message
# ---------------------------------------------------------------------------------------------


async def test_the_list_answers_for_everything_and_nothing_is_asked_per_id(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the change: a pass used to spend one provider request PER pending message.

    Both rows are on the recent page, so the per-id reader must not be asked at all — which is what
    `provider.asked` records, and why `_FakeProvider` records it.
    """
    # `_cursor` is module state carried between tests, so a prior pass can leave it past
    # these rows and the scan would skip them — the suite's own reason for this helper.
    poll.reset_cursor()
    tenant = await _tenant(maker, "listall")
    a = await _message(maker, tenant, uid="polllist0001", pmid="re_list_a", status="sent")
    b = await _message(maker, tenant, uid="polllist0002", pmid="re_list_b", status="sent")
    provider = _fake({}, monkeypatch, listed={"re_list_a": "delivered", "re_list_b": "bounced"})

    assert await poll.refresh_pending(maker) >= 2
    # Scoped to THESE ids: the table is not isolated between tests, so a pass legitimately asks
    # about rows other tests left pending. What matters is that the two the list answered for were
    # not asked about again.
    assert "re_list_a" not in provider.asked
    assert "re_list_b" not in provider.asked
    assert await _status(maker, a) == ("delivered", True, 1)
    assert (await _status(maker, b))[0] == "bounced"


async def test_a_row_missing_from_the_page_is_asked_about_not_assumed_resolved(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ABSENCE FROM THE LIST IS NOT NO-NEWS, and conflating the two would strand the oldest rows.

    The list is newest-first, unfiltered and account-wide, so a message older than the page window
    is simply not on it — and those are exactly the rows most likely to still need an answer. They
    have to fall through to the per-id call.
    """
    # `_cursor` is module state carried between tests, so a prior pass can leave it past
    # these rows and the scan would skip them — the suite's own reason for this helper.
    poll.reset_cursor()
    tenant = await _tenant(maker, "listtail")
    on_page = await _message(maker, tenant, uid="polltail0001", pmid="re_page", status="sent")
    off_page = await _message(maker, tenant, uid="polltail0002", pmid="re_old", status="sent")
    provider = _fake(
        {"re_old": "delivered"},
        monkeypatch,
        listed={"re_page": "delivered"},
        has_more=True,
    )

    assert await poll.refresh_pending(maker) >= 2
    # The one the page covered was not asked about; the one it did not was.
    assert "re_page" not in provider.asked
    assert "re_old" in provider.asked
    assert (await _status(maker, on_page))[0] == "delivered"
    assert (await _status(maker, off_page))[0] == "delivered"


async def test_a_failed_list_call_falls_back_to_asking_per_id(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blip on the list endpoint should cost efficiency, not the pass — otherwise the net stops
    catching precisely when the provider is unwell, which is when it is needed."""
    # `_cursor` is module state carried between tests, so a prior pass can leave it past
    # these rows and the scan would skip them — the suite's own reason for this helper.
    poll.reset_cursor()
    tenant = await _tenant(maker, "listblip")
    mid = await _message(maker, tenant, uid="pollblip0001", pmid="re_blip", status="sent")
    provider = _fake({"re_blip": "delivered"}, monkeypatch, listed=None)

    assert await poll.refresh_pending(maker) >= 1
    assert "re_blip" in provider.asked, "the fallback did not run when the list was unavailable"
    assert (await _status(maker, mid))[0] == "delivered"


async def test_the_scan_looks_at_the_newest_rows_because_that_is_what_the_page_holds(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan must run in the SAME direction as the provider's list, or the list cannot help.

    The list is newest-first and unfiltered, so an ascending scan handed the pass the OLDEST pending
    rows — exactly the ones that cannot be on that page. Every scanned row then missed, the whole
    batch fell through to the per-id fallback, and a pass cost one MORE request than before the list
    call existed. On the deployment the poll is FOR (webhook broken, a backlog inside the horizon)
    that is the normal case, not an edge one.

    Forced to a single-row page so the direction is the only thing under test: with `DESC` the row
    scanned is the newest — which the page covers, so nothing is asked per id. With `ASC` it would
    be some older row the page does not cover, and `asked` would not be empty.
    """
    poll.reset_cursor()
    tenant = await _tenant(maker, "order")
    newest = await _message(maker, tenant, uid="pollorder001", pmid="re_newest", status="sent")
    provider = _fake({}, monkeypatch, listed={"re_newest": "delivered"}, has_more=True)

    assert await poll.refresh_pending(maker, limit=1) == 1
    assert provider.asked == [], (
        "the scan looked at a row the provider page could not answer for — it is running in the "
        f"opposite direction to the list (asked: {provider.asked})"
    )
    assert (await _status(maker, newest))[0] == "delivered"


async def test_two_rows_on_one_provider_id_are_asked_about_as_the_newest_of_them(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A duplicated provider id is asked about ONCE, carrying the HIGHEST row's status.

    0111 left the provider-id index non-unique on purpose, so two rows can legitimately share one
    id — and `ledger.apply_provider_event` resolves such a pair to the highest id. The status the
    poll compares the answer against therefore has to come from that same row, or the decision is
    made about one row and the write lands on another.

    THE TRAP THIS PINS. The dedupe read the LAST row of the scan, which was the highest id only
    while the scan ran oldest-first. Turning it newest-first (so the rows it looks at are the ones
    the provider's page can answer for) put the highest FIRST, and a plain assignment silently
    started keeping the lowest. Here the newest row is already `delayed` and the older one is
    `sent`: reading the older one makes `email.delivery_delayed` look like progress, so the pass
    writes an event and counts the row as moved when there was nothing to learn.
    """
    poll.reset_cursor()
    tenant = await _tenant(maker, "dedupe")
    pmid = "re_dedupe_pair"
    older = await _message(maker, tenant, uid="m_dd_old", pmid=pmid, status="sent")
    newer = await _message(maker, tenant, uid="m_dd_new", pmid=pmid, status="delayed")

    provider = _fake({pmid: "delivery_delayed"}, monkeypatch)
    moved = await poll.refresh_pending(maker)

    # Scoped to THIS pair, not to the whole pass: the tests above leave their own pending rows
    # behind and the scan legitimately sweeps them too. What is under test is that one id costs one
    # request however many rows carry it.
    assert provider.asked.count(pmid) == 1, (
        "one provider id is one request, however many rows carry it"
    )
    assert moved == 0, (
        "the newest row is already `delayed`, so the provider's `delivery_delayed` moves nothing"
    )
    # Neither row gains an event: the decision is made before any write, so a poll that learned
    # nothing leaves no trace on either half of the pair.
    assert await _status(maker, newer) == ("delayed", False, 0)
    assert await _status(maker, older) == ("sent", False, 0)


async def test_a_new_message_is_left_to_the_webhook_when_one_is_configured(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a webhook secret set, a message younger than the grace is NOT polled.

    WHY THIS IS CORRECTNESS AND NOT POLITENESS. The webhook carries the provider's real event time;
    a poll can only record when it learned. `ledger.apply_provider_event` stamps
    `COALESCE(stamp, :occ, now())` — FIRST WRITER WINS — so whichever path speaks first owns the
    timestamp for ever, and `advance` then refuses to move an already-`delivered` row, so the
    webhook's real time arriving a second later is discarded rather than applied.

    That used to be safe by luck: a minute's cadence scanning OLDEST-first reached a
    seconds-old message long after it had settled. Asking every twenty seconds and scanning
    newest-first put this loop first in the queue for exactly the freshest row, which would have
    frozen a learned-at stamp on the healthy path — the same defect
    `test_the_retrieval_timestamp_is_deliberately_not_carried` exists to prevent, arrived at from
    the other side.
    """
    poll.reset_cursor()
    monkeypatch.setattr(settings, "resend_webhook_secret", "whsec_configured")
    tenant = await _tenant(maker, "grace")
    fresh = await _message(maker, tenant, uid="m_grace_new", pmid="re_grace_new", status="sent")

    provider = _fake({"re_grace_new": "delivered"}, monkeypatch)
    await poll.refresh_pending(maker)

    assert "re_grace_new" not in provider.asked, (
        "a message inside the grace must not be asked about — the webhook owns its timestamp"
    )
    assert await _status(maker, fresh) == ("sent", False, 0)

    # …and once it is older than the grace, the net does its job: the webhook has plainly not
    # come, so a learned-at stamp is better than no stamp at all.
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text(
                "UPDATE comms.messages SET created_at = now() - make_interval("
                "  secs => :age) WHERE id = :i"
            ),
            {"age": poll.WEBHOOK_GRACE_SECONDS + 60, "i": fresh},
        )

    poll.reset_cursor()
    provider = _fake({"re_grace_new": "delivered"}, monkeypatch)
    assert await poll.refresh_pending(maker) == 1
    assert await _status(maker, fresh) == ("delivered", True, 1)


async def test_with_no_webhook_secret_there_is_nothing_to_wait_for(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No secret ⇒ no grace: the poll is the ONLY source of a delivery state, not a net under one.

    `routers/resend_webhook.py` refuses every callback without a secret (`not_configured`), so
    waiting two minutes for a webhook that is switched off is pure added latency on the one
    deployment shape — a dev box — that the poll was built for. The setting is read rather than
    configured separately so the two halves cannot disagree about whether the webhook exists.
    """
    poll.reset_cursor()
    monkeypatch.setattr(settings, "resend_webhook_secret", "")
    tenant = await _tenant(maker, "nograce")
    fresh = await _message(maker, tenant, uid="m_nograce", pmid="re_nograce", status="sent")

    provider = _fake({"re_nograce": "delivered"}, monkeypatch)
    assert await poll.refresh_pending(maker) == 1
    assert provider.asked.count("re_nograce") == 1
    assert await _status(maker, fresh) == ("delivered", True, 1)


async def test_the_advanced_count_reports_what_the_ledger_wrote_not_the_prediction(
    maker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`moved` counts what the LEDGER did, not what the pass decided to ask it to do.

    The reachable divergence is a row the ledger cannot find. The pass decides from its own scan,
    then `apply_provider_event` re-resolves the provider id independently and answers False when
    nothing matches — a row pruned between the scan and the write, or an id belonging to mail sent
    by another deployment sharing this Resend account (the ledger's own docstring names both). The
    count used to be incremented regardless, so a pass that wrote nothing at all still logged
    "1 message(s) advanced from the provider's own record" — and that number is what a reader uses
    to tell a working net from a stuck one.
    """
    poll.reset_cursor()
    monkeypatch.setattr(settings, "resend_webhook_secret", "")
    tenant = await _tenant(maker, "count")
    gone = await _message(maker, tenant, uid="m_ct_gone", pmid="re_ct_gone", status="sent")

    # Deleted after the scan would select it, which is what the ledger's re-resolution is for.
    async def delete_it(*_a: object, **_k: object) -> dict[str, Any]:
        async with maker() as s2, s2.begin():
            await s2.execute(text("SET LOCAL ROLE service_role"))
            await s2.execute(text("DELETE FROM comms.messages WHERE id = :i"), {"i": gone})
        return {"re_ct_gone": poll.transport.RetrievedEmailEvent(event="delivered")}

    monkeypatch.setattr(poll.transport, "fetch_recent_events", lambda **_k: _none())
    monkeypatch.setattr(poll.transport, "fetch_last_events", delete_it)

    assert await poll.refresh_pending(maker) == 0, (
        "the ledger found nothing to write to, so the pass advanced nothing and must not say it did"
    )


async def _none() -> None:
    """`fetch_recent_events`'s "no news" answer — see `_fake`."""
    return None
