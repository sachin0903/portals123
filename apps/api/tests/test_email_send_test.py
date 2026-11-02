"""``POST /email-templates/{uid}/send-test`` — the inbox test, and the properties that make it safe.

A rendered preview shows what a browser makes of the HTML. It says nothing about Outlook's table
handling, dark-mode inversion, a client that blocks images, whether the preheader surfaces or
whether the CTA is tappable — so an author can mail the live canvas to themselves. The interesting
tests are not "does it send" but the four ways this endpoint could have been dangerous:

1. **It has no recipient parameter, and ignores an address in the body.** THE security property: an
   endpoint that took an address would be an authenticated open relay — a firm's staffer could aim
   the firm's own verified sending identity at a client with arbitrary HTML on it, and the audit
   trail would read "tested a template".
2. **It goes out as the FIRM**, resolved with ``resolve_sender`` against the session in hand, never
   ``sender_for`` (see that function; the defect it causes is pinned in
   ``tests/test_email_sender_identity.py``).
3. **A suppressed send is reported as suppressed**, not as success and not as a 500 — an author told
   "sent" by a deployment that mails nobody will spend the afternoon looking in the wrong place.
4. **No other recipient's address appears anywhere** — not in the response, not in a log line. The
   caller's own is echoed back (they need to know where to look); nothing else is.
"""

from __future__ import annotations

import json
import logging
import types
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text

from app import ratelimit
from app.config import settings
from app.email import transport as email_mod
from app.routers import email_templates as et
from tests import email_support
from tests.logcapture import captured

#: The verified sending domain — the firm's support address sits on it, so no cache could give it.
VERIFIED_FROM = "Graphene <no-reply@graphene.test>"
FIRM_SUPPORT = "hello@graphene.test"

#: An address a caller might try to smuggle in through the body. Never mailed, echoed or logged.
INTRUDER = "a.real.client@someadviserfirm.co.uk"

CANVAS = {
    "subject": "Hi {{ first_name }}",
    "preheader": "from {{ brand.name }}",
    "html": "<p class='b'>Welcome {{ first_name }}</p>",
    "css": ".b{color:#0f766e}",
}


@pytest.fixture
def staff(super_engine: Engine) -> dict[str, str]:
    """A firm with a support address on the verified domain, and one member of its staff."""
    return email_support.make_firm(
        super_engine,
        prefix="s",
        name="SFirm",
        email_domain="allowed.test",
        theme={"client": {"support": {"email": FIRM_SUPPORT}}},
    )


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], staff: dict[str, str]):
    opener, engine = email_support.session_opener(db, email_support.firm_claims(staff))
    yield opener
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clean_email_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Cold brand/sender caches, a known verified sender, empty throttle counters. The caches are
    process-global and the limiter a module singleton, so a test that inherited either would be
    asserting on the previous test's state."""
    email_mod.clear_caches()
    ratelimit.limiter.reset()
    monkeypatch.setattr(settings, "email_from", VERIFIED_FROM)
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    yield
    email_mod.clear_caches()
    ratelimit.limiter.reset()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(
        email_mod, "httpx", types.SimpleNamespace(AsyncClient=email_support.FakeClient(payloads))
    )
    return payloads


def _mails_the_author(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-production stage whose allowlist admits the author's domain (so mail can leave)."""
    monkeypatch.setattr(settings, "app_stage", "dev")
    monkeypatch.setattr(settings, "email_allowlist", ["@allowed.test"])


async def _template(session_factory, staff: dict[str, str]) -> str:
    async with session_factory() as session:
        created = await et.create_email_template(
            body=et.CreateEmailTemplate(code=f"test_{uuid.uuid4().hex[:6]}", name="Send me"),
            principal=email_support.principal(staff),
            session=session,
        )
    return str(created.uid)


async def _send(session_factory, staff: dict[str, str], uid: str, *, perms=None, **body_extra):
    async with session_factory() as session:
        return await et.send_test_email_template(
            uid=uid,
            body=et.SendTestEmail(**{**CANVAS, **body_extra}),  # type: ignore[arg-type]
            principal=email_support.principal(staff, perms),
            session=session,
        )


# ── THE SECURITY PROPERTY: the recipient is the caller, and the body cannot change that ───────


def test_the_body_model_has_no_recipient_field_and_ignores_one() -> None:
    """Pinned on the model, before any DB is involved: there is no address to set, and setting one
    anyway is discarded rather than honoured. This is the difference between "mail me my draft" and
    an authenticated open relay pointed at a firm's clients."""
    fields = set(et.SendTestEmail.model_fields)
    assert fields == {"subject", "preheader", "html", "css", "audience"}
    # The claim that actually matters, stated separately so a legitimate new field is a one-line
    # update rather than a reason to weaken this.
    assert not any(word in name for name in fields for word in ("recipient", "email", "address"))
    assert not fields & {"to", "email", "recipient", "address", "cc", "bcc"}
    body = et.SendTestEmail(**{"html": "<p>hi</p>", "to": INTRUDER, "bcc": INTRUDER})  # type: ignore[arg-type]
    assert INTRUDER not in body.model_dump_json()


@pytest.mark.asyncio
async def test_sends_to_the_callers_own_address_and_ignores_one_in_the_body(
    session_factory, staff: dict[str, str], sent: list[dict[str, Any]], monkeypatch
) -> None:
    """End to end: the mail goes to ``auth.users.email`` for ``auth.uid()``, the smuggled address
    reaches neither Resend nor the response, and the rendered canvas is the PREVIEW's own output —
    same renderer, so a test and a preview cannot disagree about what the author is looking at,
    including addressing the CALLER by name rather than the "Sarah" fixture."""
    _mails_the_author(monkeypatch)
    uid = await _template(session_factory, staff)
    out = await _send(session_factory, staff, uid, to=INTRUDER, recipient=INTRUDER)

    assert out.status == "delivered"
    assert out.to == staff["email"]
    assert [p["to"] for p in sent] == [[staff["email"]]]
    assert f"Welcome {staff['first_name']}" in sent[0]["html"]
    assert "Sarah" not in sent[0]["html"]
    assert "#0f766e" in sent[0]["html"]  # css inlined
    assert out.missing == []
    assert INTRUDER not in json.dumps(sent[0])
    assert INTRUDER not in out.model_dump_json()


@pytest.mark.asyncio
async def test_a_caller_with_no_address_on_record_is_refused(
    session_factory, staff: dict[str, str], super_engine: Engine, sent, monkeypatch
) -> None:
    """ "Send it to me" needs a "me". With no resolvable address the refusal says so — it does not
    fall back to the firm's support address, the tenant's, or anyone else's."""
    _mails_the_author(monkeypatch)
    uid = await _template(session_factory, staff)
    with super_engine.connect() as c:
        c.execute(text("UPDATE auth.users SET email = '' WHERE id = :u"), {"u": staff["user_uuid"]})
        c.commit()
    with pytest.raises(HTTPException) as ei:
        await _send(session_factory, staff, uid)
    assert ei.value.status_code == 422
    assert "your own address" in str(ei.value.detail)
    assert sent == []


# ── the subject gate ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["", "   ", "\n\t "])
async def test_a_blank_subject_is_refused_rather_than_mailed_as_a_bare_marker(
    session_factory, staff: dict[str, str], sent, monkeypatch, subject: str
) -> None:
    """A subjectless test send delivers the literal ``"[TEST] "`` and nothing else — the one thing
    an inbox shows largest, blank, on the send whose entire purpose is inbox fidelity. It is easy to
    reach: the create form offers the subject as a PLACEHOLDER rather than a value, so a template
    arrives in the editor with no subject at all.

    Refused in ``publish_version``'s own voice, because it is ``publish_version``'s own gate — a
    surface that mails a blank subject while refusing to publish one is teaching two different
    lessons about the same field."""
    _mails_the_author(monkeypatch)
    uid = await _template(session_factory, staff)
    with pytest.raises(HTTPException) as ei:
        await _send(session_factory, staff, uid, subject=subject)
    assert ei.value.status_code == 422
    assert "subject line" in str(ei.value.detail)
    assert sent == []


@pytest.mark.asyncio
async def test_the_refusal_costs_the_author_none_of_their_hourly_budget(
    session_factory, staff: dict[str, str], sent, monkeypatch
) -> None:
    """The gate is checked before the throttle, so a refused send spends nothing: it never reaches
    Resend, and the limiter exists to bound Resend spend. With a cap of one, two refusals then a
    real send must still deliver — otherwise an author who fumbled the subject twice is locked out
    of the surface for an hour by requests that cost the platform nothing."""
    _mails_the_author(monkeypatch)
    monkeypatch.setattr(settings, "rate_limit_email_test_per_hour", 1)
    uid = await _template(session_factory, staff)
    for _ in range(2):
        with pytest.raises(HTTPException) as ei:
            await _send(session_factory, staff, uid, subject="")
        assert ei.value.status_code == 422
    assert (await _send(session_factory, staff, uid)).status == "delivered"
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_subject_that_merges_to_nothing_is_reported_not_refused(
    session_factory, staff: dict[str, str], sent: list[dict[str, Any]], monkeypatch
) -> None:
    """The narrower question, answered the way publish answers it: the gate reads the AUTHORED
    subject, not the rendered one.

    A subject that is a token with no value behind it renders empty and still sends. That is
    deliberate — publish accepts it, so a canvas that would go live to real clients stays
    testable, and a refusal here would name a field the author can see is not empty. The unresolved
    token comes back in ``missing``, the diagnostic channel this response already carries."""
    _mails_the_author(monkeypatch)
    uid = await _template(session_factory, staff)
    out = await _send(session_factory, staff, uid, subject="{{ nickname }}")
    assert out.status == "delivered"
    assert out.missing == ["nickname"]
    assert sent[0]["subject"] == et.TEST_SUBJECT_PREFIX  # blank AFTER the merge, and allowed


# ── the gate, the 404 and the throttle ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("perms", [[], ["email_templates:read"]])
async def test_requires_the_write_permission(
    session_factory, staff: dict[str, str], sent, monkeypatch, perms: list[str]
) -> None:
    """Gated on ``email_templates:write`` because this spends the firm's real email quota. Read
    alone is not enough (and write implies read, so every author has it)."""
    _mails_the_author(monkeypatch)
    uid = await _template(session_factory, staff)
    with pytest.raises(HTTPException) as ei:
        await _send(session_factory, staff, uid, perms=perms)
    assert ei.value.status_code == 403
    assert sent == []


@pytest.mark.asyncio
async def test_unknown_template_404s_without_sending(
    session_factory, staff: dict[str, str], sent, monkeypatch
) -> None:
    _mails_the_author(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        await _send(session_factory, staff, "eml_does_not_exist")
    assert ei.value.status_code == 404
    assert sent == []


@pytest.mark.asyncio
async def test_the_throttle_stops_a_held_down_send_button(
    session_factory, staff: dict[str, str], sent, monkeypatch
) -> None:
    """The shared limiter (``app/ratelimit.py``), not a second throttle implementation: per acting
    user, refusing with ``Retry-After``, nothing rendered or sent past it."""
    _mails_the_author(monkeypatch)
    monkeypatch.setattr(settings, "rate_limit_email_test_per_hour", 2)
    uid = await _template(session_factory, staff)
    for _ in range(2):
        assert (await _send(session_factory, staff, uid)).status == "delivered"
    with pytest.raises(HTTPException) as ei:
        await _send(session_factory, staff, uid)
    assert ei.value.status_code == 429
    assert int((ei.value.headers or {})["Retry-After"]) > 0
    assert len(sent) == 2  # the third never reached Resend
    assert ratelimit.limiter.rejections()[ratelimit.EMAIL_TEST] == 1


# ── the three outcomes ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_suppressed_send_is_reported_as_suppressed_not_as_success(
    session_factory, staff: dict[str, str], sent, monkeypatch
) -> None:
    """The stage guard's default state on every dev and staging box: no allowlist, so no mail. The
    author is told which SETTING held it back, in a reason that names no recipient — and is still
    told what WOULD have gone, so they can see the marker and that the render worked."""
    monkeypatch.setattr(settings, "email_nonprod_guard", True)
    monkeypatch.setattr(settings, "app_stage", "staging")
    monkeypatch.setattr(settings, "email_allowlist", [])
    uid = await _template(session_factory, staff)
    out = await _send(session_factory, staff, uid)
    assert out.status == "suppressed"
    assert sent == []  # proved by nothing leaving, not by the status alone
    assert out.detail == "Email sending is unavailable. Contact support."
    assert staff["email"] not in (out.detail or "")
    assert out.subject.startswith(et.TEST_SUBJECT_PREFIX)
    assert out.to == staff["email"]


@pytest.mark.asyncio
async def test_an_unconfigured_deployment_is_suppressed_rather_than_failed(
    session_factory, staff: dict[str, str], monkeypatch
) -> None:
    """No Resend credential is a configuration state, not an incident — reporting it as ``failed``
    would send an author hunting a bug that is a missing environment variable."""
    _mails_the_author(monkeypatch)
    monkeypatch.setattr(settings, "resend_api_key", "")
    uid = await _template(session_factory, staff)
    out = await _send(session_factory, staff, uid)
    assert out.status == "suppressed"
    assert out.detail == "Email sending is unavailable. Contact support."


@pytest.mark.asyncio
async def test_a_provider_failure_is_reported_as_failed(
    session_factory, staff: dict[str, str], monkeypatch
) -> None:
    """A send that was allowed to leave and did not is the one outcome that IS an incident. The
    detail carries no provider internals — ``send_email`` logs the exception."""
    _mails_the_author(monkeypatch)
    monkeypatch.setattr(
        email_mod,
        "httpx",
        types.SimpleNamespace(AsyncClient=email_support.FakeClient([], fail=True)),
    )
    uid = await _template(session_factory, staff)
    out = await _send(session_factory, staff, uid)
    assert out.status == "failed"
    assert out.detail == "The test email could not be sent. Try again later."


# ── the envelope, the marker, and what reaches the log ────────────────────────


@pytest.mark.asyncio
async def test_it_goes_out_as_the_firm_and_is_marked_as_a_test(
    session_factory, staff: dict[str, str], sent: list[dict[str, Any]], monkeypatch
) -> None:
    """``resolve_sender`` against the session in hand, so the firm's own name AND its support
    address (on the verified domain) form the ``From``. ``sender_for`` could not have produced it:
    the caches are cold and on a miss it returns the Graphene address — the silent degradation that
    must not be reintroduced here.

    And a test send can reach a real inbox (production, or an allowlisted address), so it must be
    unmistakable there. The marker is on the SUBJECT — visible in an inbox list before anything is
    opened — and NOT in the body, which is the thing under test: an injected banner would change the
    very layout the author is checking."""
    _mails_the_author(monkeypatch)
    assert email_mod.sender_for(int(staff["tenant_id"])) == VERIFIED_FROM  # the cold-cache answer
    uid = await _template(session_factory, staff)
    out = await _send(session_factory, staff, uid)

    from_addr = sent[0]["from"]
    assert staff["firm"] in from_addr
    assert FIRM_SUPPORT in from_addr
    assert from_addr != VERIFIED_FROM

    assert sent[0]["subject"] == f"[TEST] Hi {staff['first_name']}"
    assert out.subject == sent[0]["subject"]  # the author is told what to search for
    assert "TEST" not in sent[0]["html"]


@pytest.mark.asyncio
async def test_no_other_address_reaches_the_log(
    session_factory, staff: dict[str, str], monkeypatch
) -> None:
    """A suppressed send logs at WARNING. It may carry the recipient's DOMAIN and never a local
    part — and the address the caller tried to smuggle in must not appear at all, since a log
    aggregator is exactly where that would become a leak."""
    monkeypatch.setattr(settings, "email_nonprod_guard", True)
    monkeypatch.setattr(settings, "app_stage", "dev")
    monkeypatch.setattr(settings, "email_allowlist", [])
    uid = await _template(session_factory, staff)
    with captured("app.email", level=logging.DEBUG) as records:
        await _send(session_factory, staff, uid, to=INTRUDER)
    blob = " | ".join(f"{r.getMessage()} | {r.args!r} | {r.__dict__!r}" for r in records)
    assert INTRUDER not in blob
    assert "someadviserfirm.co.uk" not in blob
    assert staff["email"] not in blob  # the full address never; the domain is allowed
    assert "allowed.test" in blob
