"""Transaction scope of the outbox drain (``app/flows/dispatcher.py``).

Sending an email is not transactional. A pass that sends N emails inside ONE transaction and only
then commits the ``status='delivered'`` rows will re-send every one of them if the process dies in
between — and the worker's container healthcheck makes a restart *more* likely, not less. So the
invariant here is ordering: each row's bookkeeping is committed before the next row's side effect
runs.

No database: the session is faked, because what is under test is the sequence of side effects and
commits, not the SQL.

The second subject is the HELD state. ``notify.*`` rows now consult ``email.sending_disabled()``
before ``notifications._send``, because when a deployment mails nobody at all — no Resend key, or a
non-production stage with an empty ``EMAIL_ALLOWLIST`` — a False from ``_send`` says
nothing about delivery, and counting it as a failure walks the row towards a DEAD LETTER: reported,
tracked, "this will never be sent" incident manufactured by a dev box. ``_notifier_loop`` already
skipped its scan for that reason; the outbox row is the same rule in the one place that can apply it
without stopping the ``task.requested`` rows this loop also drains.

Which is why the delivery tests below need ``sending_enabled``: they model a real deployment, and
they had been passing only because monkeypatching ``notifications._send`` walked straight past the
guard the real send path has always had.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from app.config import settings
from app.flows import dispatcher, notifications
from tests.logcapture import captured, messages


@pytest.fixture
def sending_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment that CAN mail — production plus a credential. Not autouse: the held-state tests
    below assert the opposite, and which of the two a test wants is the point."""
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "app_stage", "production")
    monkeypatch.setattr(settings, "email_allowlist", [])


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


def _row(row_id: int) -> dict[str, Any]:
    return {
        "id": row_id,
        "tenant_id": 1,
        "event_type": "notify.task_assigned",
        "payload": {"to": f"user{row_id}@example.com"},
        "attempts": 0,
        "max_attempts": 8,
    }


class _FakeSession:
    """One session per transaction, appending to a SHARED event log."""

    def __init__(
        self,
        log: list[str],
        queue: list[dict[str, Any]],
        updates: list[dict[str, Any]] | None = None,
    ) -> None:
        self.log = log
        self.queue = queue
        #: Every ``UPDATE flows.outbox``'s bound params, shared across the pass's sessions — a hold
        #: and a settle are both an UPDATE, and what distinguishes them is what they bind.
        self.updates = updates if updates is not None else []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def begin(self) -> _FakeSession:
        return self

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = " ".join(str(statement).split())
        if "SET LOCAL ROLE" in sql:
            self.log.append("BEGIN")
            return _Result()
        if sql.startswith("SELECT id, tenant_id"):
            claimed = self.queue[: params["lim"]]
            del self.queue[: params["lim"]]
            self.log.append(f"claim:{[r['id'] for r in claimed]}")
            return _Result(rows=claimed)
        if "UPDATE flows.outbox" in sql:
            self.updates.append(dict(params))
            # A HELD row touches neither `attempts` nor `status`; that is the whole distinction.
            held = "attempts" not in sql and "status" not in sql
            self.log.append(f"{'hold' if held else 'settle'}:{params['id']}")
            return _Result()
        return _Result()


def _maker(
    log: list[str], queue: list[dict[str, Any]], updates: list[dict[str, Any]] | None = None
) -> Any:
    def make() -> _FakeSession:
        session = _FakeSession(log, queue, updates)

        class _Txn:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *_: Any) -> None:
                log.append("COMMIT")

        session.begin = _Txn  # type: ignore[method-assign, assignment]
        return session

    return make


@pytest.mark.asyncio
async def test_each_outbox_row_is_committed_before_the_next_email_is_sent(
    monkeypatch: pytest.MonkeyPatch, sending_enabled: None
) -> None:
    """The finding: 20 sends in one transaction meant a mid-batch restart rolled back the
    bookkeeping for emails that had already left, and the retry sent them all again."""
    log: list[str] = []
    queue = [_row(1), _row(2), _row(3)]

    async def _send(tenant_id: int, payload: dict[str, Any]) -> bool:
        log.append(f"send:{payload['to']}")
        return True

    monkeypatch.setattr(notifications, "_send", _send)
    assert await dispatcher.dispatch_once(_maker(log, queue)) is True

    # Exactly one row per transaction, and no send happens with an earlier row uncommitted.
    assert log == [
        "BEGIN",
        "claim:[1]",
        "send:user1@example.com",
        "settle:1",
        "COMMIT",
        "BEGIN",
        "claim:[2]",
        "send:user2@example.com",
        "settle:2",
        "COMMIT",
        "BEGIN",
        "claim:[3]",
        "send:user3@example.com",
        "settle:3",
        "COMMIT",
        "BEGIN",
        "claim:[]",
        "COMMIT",
    ]


@pytest.mark.asyncio
async def test_a_failing_row_does_not_block_the_rest_of_the_pass(
    monkeypatch: pytest.MonkeyPatch, sending_enabled: None
) -> None:
    """Committing per row must not reintroduce head-of-line blocking: the pass continues while any
    row is still being CLAIMED, whether or not it was delivered."""
    log: list[str] = []
    queue = [_row(1), _row(2)]

    async def _send(tenant_id: int, payload: dict[str, Any]) -> bool:
        log.append(f"send:{payload['to']}")
        return payload["to"] != "user1@example.com"  # the first one fails

    monkeypatch.setattr(notifications, "_send", _send)
    assert await dispatcher.dispatch_once(_maker(log, queue)) is True
    assert "send:user2@example.com" in log
    assert log.count("COMMIT") == 3, "the failed row ended the pass instead of retrying later"


@pytest.mark.asyncio
async def test_an_empty_queue_costs_one_transaction() -> None:
    log: list[str] = []
    assert await dispatcher.dispatch_once(_maker(log, [])) is False
    assert log == ["BEGIN", "claim:[]", "COMMIT"]


# ---- dead-lettering must not be silent --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dead_lettered_row_is_reported_to_the_error_tracker() -> None:
    """A dead-lettered outbox row is a task that will NEVER be raised or an email that will NEVER
    be sent, with nothing left to retry it. It used to leave one `logger.warning` on container
    stdout — below the ERROR threshold that feeds `report_error`, the error tracker and
    `graphene_errors_total` — so the only witness to a permanently lost side effect was a grep.
    """
    log: list[str] = []
    unroutable = {**_row(7), "event_type": "who.knows", "payload": {}}
    with captured("dispatcher") as records:
        assert await dispatcher.dispatch_once(_maker(log, [unroutable])) is False
    assert "settle:7" in log, "the row was never settled"
    errors = [r for r in records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, f"dead-lettering emitted no ERROR: {messages(records)}"
    assert errors[0].event == "outbox_dead_lettered"
    assert errors[0].outbox_id == 7
    assert errors[0].event_type == "who.knows"
    assert errors[0].permanent is True
    assert "NEVER be delivered" in errors[0].getMessage()


@pytest.mark.asyncio
async def test_a_row_still_on_backoff_is_not_reported_as_a_dead_letter(
    monkeypatch: pytest.MonkeyPatch, sending_enabled: None
) -> None:
    """The alarm has to mean "lost for good". A row that failed once and will be retried is not
    lost, so reporting it would train an operator to ignore the one that is."""
    log: list[str] = []

    async def _send(tenant_id: int, payload: dict[str, Any]) -> bool:
        return False

    monkeypatch.setattr(notifications, "_send", _send)
    with captured("dispatcher") as records:
        await dispatcher.dispatch_once(_maker(log, [_row(1)]))  # attempts 0 -> 1 of 8
    assert [r for r in records if r.levelno >= logging.ERROR] == []

    # ...and the LAST attempt, which does exhaust max_attempts, is reported.
    exhausted = {**_row(2), "attempts": 7, "max_attempts": 8}
    with captured("dispatcher") as records:
        await dispatcher.dispatch_once(_maker(log, [exhausted]))
    errors = [r for r in records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].attempts == 8
    assert errors[0].permanent is False


@pytest.mark.asyncio
async def test_dispatch_due_still_reports_the_delivered_count(
    monkeypatch: pytest.MonkeyPatch, sending_enabled: None
) -> None:
    """`dispatch_due` is the batching entry point other tests and callers use; splitting the drain
    out must not change its contract."""
    log: list[str] = []
    queue = [_row(1), _row(2)]

    async def _send(tenant_id: int, payload: dict[str, Any]) -> bool:
        return True

    monkeypatch.setattr(notifications, "_send", _send)
    session = _FakeSession(log, queue)
    delivered = await dispatcher.dispatch_due(session, now=datetime.now(UTC), limit=20)
    assert delivered == 2


# ---- HELD is not FAILED ------------------------------------------------------------------------


@pytest.fixture
def sending_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-production stage with an empty ``EMAIL_ALLOWLIST``: a Resend key is present, because
    "no key" is the easy case and the interesting one is a staging box that HAS a working credential
    and still must mail nobody. The empty list is set here on purpose — the shipped default is
    ``@grapheneplatforms.com``, which would let a Graphene address through."""
    monkeypatch.setattr(settings, "email_nonprod_guard", True)
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "app_stage", "staging")
    monkeypatch.setattr(settings, "email_allowlist", [])


@pytest.mark.asyncio
async def test_a_notify_row_is_held_not_failed_when_the_deployment_mails_nobody(
    monkeypatch: pytest.MonkeyPatch, sending_disabled: None
) -> None:
    """The asymmetry this closes: ``_notifier_loop`` skips its scan under suppression, so a dev
    box's task notices stay quietly unsent — but the outbox had no such check, so every ``notify.*``
    row
    was handed to ``_send``, answered False for a reason that is not a delivery failure, and burned
    an attempt. Eight passes later it DEAD-LETTERS: reported to the error tracker, counted in
    ``graphene_errors_total``, and worded "will NEVER be delivered" — indistinguishable from a real
    Resend outage, on a deployment where nothing was ever going to be sent.

    A held row keeps its attempt count and its ``queued`` status; only ``next_retry`` moves."""
    log: list[str] = []
    updates: list[dict[str, Any]] = []

    async def _send(tenant_id: int, payload: dict[str, Any]) -> bool:  # pragma: no cover
        raise AssertionError("a held row must not be handed to the sender at all")

    monkeypatch.setattr(notifications, "_send", _send)
    with captured("dispatcher") as records:
        assert await dispatcher.dispatch_once(_maker(log, [_row(1)], updates)) is False

    assert log.count("hold:1") == 1, f"the row was not held: {log}"
    assert "settle:1" not in log, "a held row was settled — that is what burns the attempt"
    assert [r for r in records if r.levelno >= logging.ERROR] == [], (
        "holding a row reported an error; a deployment that mails nobody is a configuration, "
        "not an incident"
    )
    (hold,) = updates
    assert set(hold) == {"now", "delay", "err", "id"}, (
        f"a hold must bind neither attempts nor status: {sorted(hold)}"
    )
    assert hold["delay"] == dispatcher.HELD_RETRY_SECONDS
    assert "held (not failed)" in hold["err"]
    assert "EMAIL_ALLOWLIST is empty" in hold["err"]  # names the setting, not the recipient
    assert "user1@example.com" not in hold["err"]


@pytest.mark.asyncio
async def test_holding_an_email_row_still_drains_the_rows_that_are_not_email(
    monkeypatch: pytest.MonkeyPatch, sending_disabled: None
) -> None:
    """WHY the check is on the row and not on the worker's outbox loop. That loop also drains
    ``task.requested`` (and ``aml.screen.requested``, and ``webhook_out``), none of which sends
    mail, so a loop-level ``sending_disabled()`` skip would stop a dev box raising flow tasks.

    And the hold must move ``next_retry``: a bare "leave it alone and come back" would have the same
    row re-claimed on every one of the pass's twenty iterations, since the claim is ordered by
    ``next_retry`` — head-of-line blocking the task rows behind it."""
    log: list[str] = []
    raised: list[str] = []

    async def _task(session: Any, tenant_id: int, payload: dict[str, Any]) -> None:
        raised.append(str(payload.get("source_ref")))

    monkeypatch.setattr(dispatcher, "_handle_task_requested", _task)
    task_row = {
        **_row(2),
        "event_type": "task.requested",
        "payload": {"spec": {}, "source_ref": "flow:step:1"},
    }
    assert await dispatcher.dispatch_once(_maker(log, [_row(1), task_row])) is True
    assert raised == ["flow:step:1"], "the task row never dispatched"
    assert log.count("hold:1") == 1
