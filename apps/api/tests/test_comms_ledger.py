"""The outbound-email ledger: the monotonic status rule, and that every send outcome is recorded.

Two halves, and the split matters. The status rule (:func:`ledger.advance`) is pure and is tested
exhaustively here without a database, because it is the whole correctness of the delivery timeline
and a webhook test would only reach it through three layers of I/O. The recording half is tested
through ``send_email`` itself with ``ledger.record`` captured — because the claim worth pinning is
not "the ledger can insert a row", it is "no exit path of the chokepoint fails to record", and only
driving the real function proves that.

The RLS half lives in ``test_comms_rls.py`` (it needs real Postgres); the structural "send_email
still calls the ledger" scan lives in ``test_email_send_guard.py`` beside the guard scan it mirrors.
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any

import pytest

from app.comms import ledger
from app.config import settings
from app.email import transport as email_mod
from tests import email_support

RECIPIENT = "someone@example.com"


# ---------------------------------------------------------------------------------------------
# The pure rule
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "incoming", "expected"),
    [
        # Forwards through the rank.
        ("sent", "delayed", "delayed"),
        ("sent", "delivered", "delivered"),
        ("delayed", "delivered", "delivered"),
        # BACKWARDS IS REFUSED. This is the case that actually happens: Resend delivers at least
        # once and out of order, so a redelivered `email.sent` routinely arrives after
        # `email.delivered`. Letting it through would show a delivered email as merely sent.
        ("delivered", "sent", None),
        ("delivered", "delayed", None),
        ("delayed", "sent", None),
        # The same status twice — a plain redelivery — moves nothing.
        ("delivered", "delivered", None),
        ("bounced", "bounced", None),
        # A bounce or complaint wins from ANY rank. A deferred address that later hard-bounces is
        # genuinely bounced, and that is the more important fact than "we once delivered to it".
        ("sent", "bounced", "bounced"),
        ("delivered", "bounced", "bounced"),
        ("delayed", "bounced", "bounced"),
        ("delivered", "complained", "complained"),
        # ...but nothing walks a bounce back, however out of order the callbacks arrive.
        ("bounced", "delivered", None),
        ("bounced", "sent", None),
        ("complained", "delivered", None),
        # Nothing was ever posted, so no provider callback may move these. (Unreachable in practice
        # — such a row has no provider_message_id to match — but the rule is written down rather
        # than left resting on that coincidence.)
        ("suppressed", "delivered", None),
        ("unconfigured", "sent", None),
        ("failed", "delivered", None),
        ("suppressed", "bounced", None),
    ],
)
def test_advance_only_ever_moves_forwards(
    current: str, incoming: str, expected: str | None
) -> None:
    assert ledger.advance(current, incoming) == expected


def test_the_status_tables_agree_with_each_other() -> None:
    """Five constants describe one vocabulary, and nothing but this checks they still line up.

    A status a callback can produce with no column to stamp it in raises a KeyError on a live
    webhook; a status that is both ranked AND overriding makes `advance` incoherent. Both fail the
    moment someone adds a status and updates only one table.
    """
    produced = {status for status in ledger.EVENT_STATUS.values() if status is not None}
    assert produced <= set(ledger.STAMP_COLUMN)
    ranked = set(ledger.STATUS_RANK)
    assert not (ranked & ledger.OVERRIDING)
    assert not (ranked & ledger.TERMINAL_AT_INSERT)
    assert not (ledger.OVERRIDING & ledger.TERMINAL_AT_INSERT)


# ---------------------------------------------------------------------------------------------
# The context
# ---------------------------------------------------------------------------------------------


def test_recipient_kind_is_derived_not_declared() -> None:
    """The kind and the ids cannot disagree, because the kind is computed from them — which is what
    ``messages_recipient_ref`` refuses at the database and what a hand-set kind would eventually
    get wrong."""
    assert ledger.SendContext().recipient_kind == "external"
    assert ledger.SendContext(recipient_contact_id=7).recipient_kind == "contact"
    assert ledger.SendContext(recipient_user_id="u-1").recipient_kind == "user"
    # A contact wins if somehow both are set: the client is the person the mail is about.
    assert ledger.SendContext(recipient_contact_id=7, recipient_user_id="u-1").recipient_kind == (
        "contact"
    )


def test_an_oversized_body_is_dropped_whole_never_truncated() -> None:
    """Half an email renders as a broken one, and a broken preview reads as corruption rather than
    as "too big to keep"."""
    assert ledger._capped(None) is None
    assert ledger._capped("") is None
    assert ledger._capped("<p>hi</p>") == "<p>hi</p>"
    assert ledger._capped("x" * (ledger.MAX_HTML_BYTES + 1)) is None
    # Measured in BYTES, not characters — a body of multi-byte characters is what would slip past a
    # len() check and then fail the column.
    assert ledger._capped("é" * ledger.MAX_HTML_BYTES) is None


def test_an_oversized_request_elides_the_body_html_before_giving_up() -> None:
    """The request's copy of html is the part that blows the budget, so it goes first rather than
    losing from/to/subject with it — but it goes as a MARKER, not as a missing key.

    A Raw pane showing a POST with no `html` at all reads as "we sent an empty email", which is a
    worse answer than "the body was too big to keep" and indistinguishable from a rendering bug.
    """
    small = {"method": "POST", "body": {"to": ["a@b.test"], "html": "<p>hi</p>"}}
    assert ledger._capped_request(small) is not None
    huge_html = "x" * (ledger.MAX_HTML_BYTES + 1)
    dumped = ledger._capped_request(
        {"method": "POST", "body": {"to": ["a@b.test"], "html": huge_html}}
    )
    assert dumped is not None
    parsed = json.loads(dumped)
    assert parsed["body"]["html"] == ledger.ELIDED_HTML
    assert parsed["body"]["to"] == ["a@b.test"]
    assert ledger._capped_request(None) is None


def test_a_value_json_cannot_serialise_does_not_cost_the_row() -> None:
    """`_capped_request` runs inside `record`'s try block, so a TypeError here would be swallowed
    there and the send — which has already happened — would get NO ledger row at all. An optional
    diagnostic column must never take the required record down with it."""
    from decimal import Decimal

    dumped = ledger._capped_request({"method": "POST", "body": {"amount": Decimal("1.50")}})
    assert dumped is not None
    assert json.loads(dumped)["body"]["amount"] == "1.50"


def test_the_recorded_request_drops_attachment_bytes() -> None:
    """Inlined images travel as base64 on the wire. Storing them would dwarf every other column
    for no preview gain — the filenames and content-ids are what Raw needs to show they went."""
    recorded = email_mod._request_for_ledger(
        {
            "from": "a@b.test",
            "to": ["c@d.test"],
            "subject": "Hi",
            "html": "<p>x</p>",
            "attachments": [{"filename": "logo.png", "content": "aaa", "content_id": "img1"}],
        }
    )
    assert recorded["method"] == "POST"
    assert recorded["url"] == email_mod.RESEND_ENDPOINT
    assert recorded["body"]["attachments"] == [{"filename": "logo.png", "content_id": "img1"}]
    # The POST payload itself is untouched — stripping is a copy for the ledger.
    assert "content" not in recorded["body"]["attachments"][0]


# ---------------------------------------------------------------------------------------------
# Every exit path of the chokepoint records
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Captures ``ledger.record`` instead of writing. Patched on the ledger module itself, which is
    the object ``app.email.transport`` resolves through, so the real call path is exercised."""
    calls: list[dict[str, Any]] = []

    # Mirrors `ledger.record`'s real signature rather than swallowing **kwargs, so the defaults are
    # materialised (a caller that omits `provider_message_id` still yields the key) AND a change to
    # that signature fails here loudly instead of silently capturing nothing.
    async def fake_record(
        ctx: ledger.SendContext | None,
        *,
        to: str,
        subject: str,
        status: str,
        html: str | None = None,
        request: dict[str, Any] | None = None,
        provider_message_id: str | None = None,
        error: str | None = None,
    ) -> None:
        calls.append(
            {
                "ctx": ctx,
                "to": to,
                "subject": subject,
                "status": status,
                "html": html,
                "request": request,
                "provider_message_id": provider_message_id,
                "error": error,
            }
        )

    monkeypatch.setattr(ledger, "record", fake_record)
    return calls


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A configured Resend whose POST is captured. Mirrors ``test_email_send_guard``'s fixture."""
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "email_nonprod_guard", False)
    monkeypatch.setattr(
        email_mod, "httpx", types.SimpleNamespace(AsyncClient=email_support.FakeClient(payloads))
    )
    return payloads


async def test_a_successful_send_records_sent_with_the_provider_id(
    configured: list[dict[str, Any]], recorded: list[dict[str, Any]]
) -> None:
    """The provider id is the entire point: it is the only handle the delivery webhook has for
    matching a callback back to this send, and it was previously thrown away."""
    assert await email_mod.send_email(to=RECIPIENT, subject="Hello", html="<p>hi</p>") is True
    assert len(configured) == 1
    assert len(recorded) == 1
    assert recorded[0]["status"] == "sent"
    assert recorded[0]["provider_message_id"] == "re_fake_message_id"
    assert recorded[0]["to"] == RECIPIENT
    assert recorded[0]["error"] is None
    request = recorded[0]["request"]
    assert request is not None
    assert request["method"] == "POST"
    assert request["url"] == email_mod.RESEND_ENDPOINT
    assert request["body"]["to"] == [RECIPIENT]
    assert request["body"]["subject"] == "Hello"
    # The body is stored ONCE: `html` above holds it, so a wire copy identical to it is elided.
    assert request["body"]["html"] == email_mod.SAME_AS_PREVIEW
    assert "re_test_key" not in str(request)


async def test_a_suppressed_send_is_recorded_and_names_the_setting(
    monkeypatch: pytest.MonkeyPatch,
    configured: list[dict[str, Any]],
    recorded: list[dict[str, Any]],
) -> None:
    """Suppression is an OUTCOME, not a gap. A dev box whose whole ledger reads 'suppressed' must
    say which setting decided that, or the Activity feed reads as "every email failed"."""
    monkeypatch.setattr(settings, "email_nonprod_guard", True)
    monkeypatch.setattr(settings, "app_stage", "dev")
    monkeypatch.setattr(settings, "email_allowlist", [])

    assert await email_mod.send_email(to=RECIPIENT, subject="Hello", html="<p>hi</p>") is False
    assert configured == [], "nothing may leave the building when the guard suppresses"
    assert len(recorded) == 1
    assert recorded[0]["status"] == "suppressed"
    assert recorded[0]["provider_message_id"] is None
    assert "EMAIL_ALLOWLIST" in (recorded[0]["error"] or "")
    # The reason names the setting and NEVER the recipient — it is written into a log line too.
    assert RECIPIENT not in (recorded[0]["error"] or "")


async def test_an_unconfigured_deployment_still_records(
    monkeypatch: pytest.MonkeyPatch, recorded: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(settings, "resend_api_key", "")
    assert await email_mod.send_email(to=RECIPIENT, subject="Hello", html="<p>hi</p>") is False
    assert len(recorded) == 1
    assert recorded[0]["status"] == "unconfigured"


async def test_a_failed_send_records_failed_and_not_sent(
    monkeypatch: pytest.MonkeyPatch, recorded: list[dict[str, Any]]
) -> None:
    """The except branch. A send that raised must never be recorded as sent — the ledger asserting
    the opposite of what happened is worse than no ledger."""
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "email_nonprod_guard", False)
    monkeypatch.setattr(
        email_mod,
        "httpx",
        types.SimpleNamespace(AsyncClient=email_support.FakeClient(payloads, fail=True)),
    )
    assert await email_mod.send_email(to=RECIPIENT, subject="Hello", html="<p>hi</p>") is False
    assert len(recorded) == 1
    assert recorded[0]["status"] == "failed"
    assert "resend is down" in (recorded[0]["error"] or "")
    # The bearer token is in scope in that branch and must not reach the ledger any more than it
    # reaches the log.
    assert "re_test_key" not in (recorded[0]["error"] or "")
    assert "re_test_key" not in str(recorded[0]["request"] or "")


async def test_a_malformed_recipient_records_nothing(
    configured: list[dict[str, Any]], recorded: list[dict[str, Any]]
) -> None:
    """The ONE path that deliberately does not record. `to_address` is NOT NULL and would have to
    hold this value — and the whole reason this branch refuses is that we could not decide what the
    value means, so we cannot say which part of it is safe to store."""
    assert (
        await email_mod.send_email(to="one@a.com, two@b.com", subject="Hello", html="<p>hi</p>")
        is False
    )
    assert configured == []
    assert recorded == []


async def test_the_context_reaches_the_ledger(
    configured: list[dict[str, Any]], recorded: list[dict[str, Any]]
) -> None:
    """WHY an email was sent is what the Activity feed filters on and what the event registry counts
    by, so it has to survive the trip through the chokepoint."""
    ctx = ledger.SendContext(
        tenant_id=42,
        template_code="document_sent",
        event_type="document.sent",
        rule_uid="nrule_x",
        category="documents",
        recipient_contact_id=7,
        source="worker",
    )
    await email_mod.send_email(to=RECIPIENT, subject="Hello", html="<p>hi</p>", ctx=ctx)
    assert recorded[0]["ctx"] is ctx


async def test_an_unthreaded_call_site_still_produces_a_row(
    configured: list[dict[str, Any]], recorded: list[dict[str, Any]]
) -> None:
    """`ctx` is optional so a forgotten call site degrades to an UNATTRIBUTED row — visible to
    Graphene operations — rather than to a hole in the ledger nobody can see."""
    await email_mod.send_email(to=RECIPIENT, subject="Hello", html="<p>hi</p>")
    assert recorded[0]["ctx"] is None


async def test_a_body_is_stored_for_the_preview_not_the_wire_version(
    configured: list[dict[str, Any]], recorded: list[dict[str, Any]]
) -> None:
    """`send_email` inlines our own images as `cid:` attachments before posting. The LEDGER keeps
    the original: `cid:` references resolve only inside a real mail client, so storing the wire
    version would give the Activity preview a body full of broken images."""
    html = '<p>hi</p><img src="https://example.invalid/logo.png">'
    await email_mod.send_email(to=RECIPIENT, subject="Hello", html=html)
    assert recorded[0]["html"] == html
    # Nothing was inlined (the fixture origin is not ours), so the wire body IS this string — and
    # is therefore elided rather than stored a second time. The pane splices it back for Raw.
    assert recorded[0]["request"]["body"]["html"] == email_mod.SAME_AS_PREVIEW


# ---------------------------------------------------------------------------------------------
# apply_provider_event, against real Postgres
# ---------------------------------------------------------------------------------------------
# The pure `advance` rule above says what SHOULD happen; these say that the SQL carrying it out
# actually does. Both halves are needed: the UPDATE writes the status and the stamp in one
# statement, and a COALESCE in the wrong place would pass every test above and still lose the
# timeline.


async def _mk_message(session: Any, uid: str, pmid: str, status: str = "sent") -> int:
    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO comms.messages (uid, tenant_id, recipient_kind, to_address, to_domain, "
            "  subject, status, provider_message_id) "
            "VALUES (:uid, NULL, 'external', 'a@b.test', 'b.test', 'probe', :st, :pmid)"
        ),
        {"uid": uid, "st": status, "pmid": pmid},
    )
    return int(
        (
            await session.execute(
                text("SELECT id FROM comms.messages WHERE uid = :uid"), {"uid": uid}
            )
        ).scalar_one()
    )


async def _state(session: Any, mid: int) -> dict[str, Any]:
    from sqlalchemy import text

    row = (
        (
            await session.execute(
                text(
                    "SELECT status, sent_at, delivered_at, bounced_at, "
                    "  (SELECT count(*) FROM comms.message_events WHERE message_id = :m) AS events "
                    "FROM comms.messages WHERE id = :m"
                ),
                {"m": mid},
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def test_a_redelivered_callback_is_a_no_op(service_session: Any) -> None:
    """A callback advances the status and stamps it — ONCE, however many times it arrives.

    Resend delivers at least once, so a repeat is the ordinary case rather than an edge one; the
    unique index on (message_id, provider_event_id) is what collapses it.
    """
    mid = await _mk_message(service_session, "dbidem00001", "pm_idem_1")
    for _ in range(3):
        await ledger.apply_provider_event(
            service_session,
            provider_message_id="pm_idem_1",
            event_type="email.delivered",
            provider_event_id="svix_same",
            occurred_at=None,
            payload={},
        )
    state = await _state(service_session, mid)
    assert state["events"] == 1, "a redelivery must not add a second timeline entry"
    assert state["status"] == "delivered"
    assert state["delivered_at"] is not None


async def test_a_late_sent_callback_does_not_walk_a_delivered_row_back(
    service_session: Any,
) -> None:
    """The failure this whole rule exists for: callbacks arrive out of order, so `email.sent`
    routinely lands after `email.delivered`."""
    mid = await _mk_message(service_session, "dbback00001", "pm_back_1")
    await ledger.apply_provider_event(
        service_session,
        provider_message_id="pm_back_1",
        event_type="email.delivered",
        provider_event_id="svix_d",
        occurred_at=None,
        payload={},
    )
    await ledger.apply_provider_event(
        service_session,
        provider_message_id="pm_back_1",
        event_type="email.sent",
        provider_event_id="svix_s",
        occurred_at=None,
        payload={},
    )
    state = await _state(service_session, mid)
    assert state["status"] == "delivered", "the status must not regress"
    # ...but the stamp IS filled, because when it was sent is a new fact and the timeline wants it.
    assert state["sent_at"] is not None
    assert state["events"] == 2


async def test_a_bounce_wins_from_delivered(service_session: Any) -> None:
    mid = await _mk_message(service_session, "dbbnce00001", "pm_bnce_1")
    for et, peid in (("email.delivered", "s1"), ("email.bounced", "s2")):
        await ledger.apply_provider_event(
            service_session,
            provider_message_id="pm_bnce_1",
            event_type=et,
            provider_event_id=peid,
            occurred_at=None,
            payload={},
        )
    state = await _state(service_session, mid)
    assert state["status"] == "bounced"
    assert state["bounced_at"] is not None
    assert state["delivered_at"] is not None, "the delivery still happened and is still recorded"


async def test_an_unknown_provider_message_id_is_reported_not_raised(
    service_session: Any,
) -> None:
    assert (
        await ledger.apply_provider_event(
            service_session,
            provider_message_id="pm_does_not_exist",
            event_type="email.delivered",
            provider_event_id="svix_x",
            occurred_at=None,
            payload={},
        )
        is False
    )


# ---------------------------------------------------------------------------------------------
# No read receipts
# ---------------------------------------------------------------------------------------------


def test_opens_and_clicks_are_not_a_status_this_ledger_holds() -> None:
    """This product does not do read receipts, and the vocabulary is where that is enforced.

    An open is a tracking pixel loading: Apple Mail Privacy Protection fires it for every Apple
    Mail user whether or not a human looked, and a client blocking images never fires it however
    carefully the mail was read. Evidence in neither direction, bought by putting a tracking pixel
    in a client's email. Delivered is the last thing we can honestly claim.
    """
    for gone in ("opened", "clicked"):
        assert gone not in ledger.STATUS_RANK
        assert gone not in ledger.STAMP_COLUMN
    assert not {"email.opened", "email.clicked"} & set(ledger.EVENT_STATUS)
    # And refused at the DOOR rather than stored-and-ignored: a `comms.message_events` row would
    # hold the provider's own tracking record, which is the thing being declined. `NOT_TRACKED` is
    # what the route and the poll both consult, before either writes anything.
    assert ledger.NOT_TRACKED == frozenset({"email.opened", "email.clicked"})


# ---------------------------------------------------------------------------------------------
# The body is stored once
# ---------------------------------------------------------------------------------------------


def test_the_wire_body_is_stored_only_when_it_differs_from_the_preview() -> None:
    """The de-duplication: the request we record is the wire payload, and its `html` is USUALLY
    byte-identical to the body already stored on the row — measured at 3,952 bytes of exactly the
    same markup. Storing both doubles the table for nothing.

    So an identical body is replaced by a marker, and a CHANGED one is kept in full — inlining
    rewrites image URLs into `cid:` references, and "did the logo actually go as an attachment" is
    answerable only from the markup that referenced it.
    """

    def recorded(*, wire: str, preview: str) -> dict:
        payload = email_mod._wire_payload(
            address="a@b.test", subject="s", html=wire, sender=None, reply_to=None
        )
        body = email_mod._request_for_ledger(payload, preview_html=preview)["body"]
        assert isinstance(body, dict)
        return body

    same = recorded(wire="<p>the body</p>", preview="<p>the body</p>")
    assert same["html"] == email_mod.SAME_AS_PREVIEW
    # The envelope is the part worth keeping — `from` and `reply_to` are what a reconstruction
    # cannot invent.
    assert same["to"] == ["a@b.test"]
    assert same["subject"] == "s"

    inlined = recorded(
        wire='<p><img src="cid:logo"></p>',
        preview='<p><img src="https://cdn.example.test/logo.png"></p>',
    )
    assert inlined["html"] == '<p><img src="cid:logo"></p>'


@pytest.mark.pkgs_ui_pin
def test_the_marker_is_the_same_string_the_portal_looks_for() -> None:
    """One decision, two languages. The pane splices the stored body back in when it sees this
    exact string; a silent drift would render the marker literally in the Raw tab."""
    ts = (
        Path(__file__).resolve().parents[3] / "pkgs/ui/src/lib/components/comms/message-detail.ts"
    ).read_text(encoding="utf-8")
    assert f"'{email_mod.SAME_AS_PREVIEW}'" in ts
