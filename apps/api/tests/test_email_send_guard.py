"""The non-production send guard — and the tripwire that keeps it a chokepoint.

THE STANDING CONSTRAINT: on ``dev`` and ``staging`` we mail only ``@grapheneplatforms.com`` unless
a deployment says otherwise. ``EMAIL_NONPROD_GUARD`` ships on, and an unset ``EMAIL_ALLOWLIST`` is
that domain, so a dev or staging box that inherits a working ``RESEND_API_KEY`` still cannot mail
the address on a client's record. ``send_email`` asks ``suppression_reason`` before it asks Resend
anything. An EMPTY list — an explicit blank, not the unset default — suppresses EVERYTHING on
those two stages: a cleared value means "mail nobody", never "mail everybody". Every other stage,
including production, mails freely. The plausible regression is not "the guard is missing" but
"the guard was made convenient" — an empty list read as "no restriction configured", a domain
entry matching subdomains, the guard widened to every non-production name, or the switch flipped
back off.

Three further rules:

* **The value CHECKED is the value SENT.** The guard used to validate a normalised derivation of
  ``to`` while the payload posted the raw original, so a ``to`` naming two people was checked as if
  it named the second and then sent to both. ``_single_address`` refuses anything that does not name
  exactly one mailbox, and ``send_email`` posts what it returns.
* **Suppression is an outcome, not an error.** ``send_email`` returns False and never raises, so a
  flow, the task notifier or an invite does not break because it ran in dev (False already meant
  "not delivered"). What a caller must not do is treat it as a FAULT —
  ``tests/test_effects.py`` pins the flow action's side of that.
* **No recipient address reaches the log** — only its domain, enough to tell "the allowlist is
  wrong" from "the stage is wrong". A recipient we could not parse gets not even that.

The last section is the TRIPWIRE. A guard on one function is only a chokepoint while that function
is the only way out: a second module holding the Resend credential, importing a Resend SDK or
naming its endpoint would be a second route from a staging box to a real inbox, past every test
above. That is a claim about the source tree, so something has to read the source tree.
"""

from __future__ import annotations

import ast
import logging
import types
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from app.config import Settings, settings
from app.email import transport as email_mod
from tests import email_support
from tests.logcapture import captured

ADDRESS = "Craig Currie <craig@grapheneplatforms.com>"
BARE = "craig@grapheneplatforms.com"
OUTSIDER = "a.real.client@someadviserfirm.co.uk"


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A configured Resend whose HTTP call is captured rather than made. Every test that expects
    suppression asserts this stayed EMPTY — so "suppressed" is proved by nothing leaving, not by the
    return value alone."""
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(
        email_mod, "httpx", types.SimpleNamespace(AsyncClient=email_support.FakeClient(payloads))
    )
    return payloads


@pytest.fixture
def failing_sends(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A configured Resend whose POST raises — ``send_email``'s ``except`` branch, reached."""
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(
        email_mod,
        "httpx",
        types.SimpleNamespace(AsyncClient=email_support.FakeClient(payloads, fail=True)),
    )
    return payloads


def _stage(monkeypatch: pytest.MonkeyPatch, stage: str, allowlist: list[str]) -> None:
    monkeypatch.setattr(settings, "email_nonprod_guard", True)
    monkeypatch.setattr(settings, "app_stage", stage)
    monkeypatch.setattr(settings, "email_allowlist", allowlist)


async def _send(to: str = ADDRESS) -> bool:
    return await email_mod.send_email(to=to, subject="Set your password", html="<p>hi</p>")


def _record_text(record: logging.LogRecord) -> str:
    """Everything a log shipper could serialise from one record: the rendered message, the raw args,
    and any structured extras we attached."""
    extras = {
        key: value
        for key, value in record.__dict__.items()
        if key not in logging.LogRecord("", 0, "", 0, "", None, None).__dict__
    }
    return f"{record.getMessage()} | {record.args!r} | {extras!r}"


# ── production is untouched ───────────────────────────────────────────────────


async def test_production_sends_to_anyone_and_reads_no_allowlist(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not become a production feature by accident — including when a list is left in
    the production deploy config, since quietly dropping real client mail would be worse than the
    problem it solves."""
    _stage(monkeypatch, "production", [])
    assert await _send(OUTSIDER) is True
    assert [p["to"] for p in sent] == [[OUTSIDER]]
    assert email_mod.suppression_reason(OUTSIDER) is None
    assert email_mod.sending_disabled() is None

    _stage(monkeypatch, "production", ["@grapheneplatforms.com"])
    assert await _send(OUTSIDER) is True
    assert len(sent) == 2


# ── the guard ships ON ────────────────────────────────────────────────────────


async def test_the_guard_is_on_by_default_and_mails_only_the_graphene_domain(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``EMAIL_NONPROD_GUARD`` ships True and an unset ``EMAIL_ALLOWLIST`` is
    ``@grapheneplatforms.com``, so dev and staging mail Graphene addresses and nobody else without a
    deploy entry.

    The defaults are read off the FIELD, not off the loaded settings. A developer's ``.env.local``
    can set either variable, and that must not make this test fail on the machines that opted out
    while CI stayed green. The behavioural half then runs with the defaults applied explicitly."""
    assert Settings.model_fields["email_nonprod_guard"].default is True
    assert Settings.model_fields["email_allowlist"].default == ["@grapheneplatforms.com"]
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "email_nonprod_guard", True)
    monkeypatch.setattr(settings, "email_allowlist", ["@grapheneplatforms.com"])
    for stage in ("dev", "staging"):
        monkeypatch.setattr(settings, "app_stage", stage)
        sent.clear()
        assert await _send(BARE) is True
        assert await _send(OUTSIDER) is False
        assert [p["to"] for p in sent] == [[BARE]]


async def test_a_saved_list_replaces_the_default(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The admin page's list is who gets mail. ``@grapheneplatforms.com`` is only the default."""
    monkeypatch.setattr(settings, "app_stage", "dev")
    monkeypatch.setattr(settings, "email_nonprod_guard", True)
    monkeypatch.setattr(settings, "email_allowlist", ["@grapheneplatforms.com"])
    email_mod.set_runtime_allowlist(["@someadviserfirm.co.uk"])
    assert email_mod.suppression_reason(BARE) is not None
    assert await _send(OUTSIDER) is True
    assert await _send(BARE) is False
    assert [p["to"] for p in sent] == [[OUTSIDER]]
    email_mod.clear_runtime_allowlist()
    assert email_mod.suppression_reason(BARE) is None


def test_a_saved_empty_list_mails_nobody_even_when_the_default_would(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_stage", "staging")
    monkeypatch.setattr(settings, "email_nonprod_guard", True)
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "email_allowlist", ["@grapheneplatforms.com"])
    email_mod.set_runtime_allowlist([])
    assert "EMAIL_ALLOWLIST is empty" in (email_mod.suppression_reason(BARE) or "")
    assert "EMAIL_ALLOWLIST is empty" in (email_mod.sending_disabled() or "")


def test_allowlist_entries_normalise_to_one_address_or_domain() -> None:
    assert email_mod.normalise_allowlist_entries(
        [" QA@Example.com ", "@Example.com", "example.com", "", "  "]
    ) == ["qa@example.com", "@example.com"]
    with pytest.raises(email_mod.AllowlistEntryError):
        email_mod.normalise_allowlist_entries(["not an address"])
    with pytest.raises(email_mod.AllowlistEntryError):
        email_mod.normalise_allowlist_entries(["a@b@c"])


async def test_the_saved_allowlist_roundtrips_and_reset_removes_it(service_session: Any) -> None:
    assert await email_mod.read_stored_allowlist(service_session) is None
    await email_mod.write_stored_allowlist(service_session, ["qa@example.com"])
    assert await email_mod.read_stored_allowlist(service_session) == ["qa@example.com"]
    await email_mod.write_stored_allowlist(service_session, [])
    assert await email_mod.read_stored_allowlist(service_session) == []
    await email_mod.clear_stored_allowlist(service_session)
    assert await email_mod.read_stored_allowlist(service_session) is None


async def test_turning_the_guard_off_mails_anyone(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``EMAIL_NONPROD_GUARD=false`` is the break-glass: a non-production stage mails freely, gated
    only by ``RESEND_API_KEY``, even when the allowlist is empty."""
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "app_stage", "dev")
    monkeypatch.setattr(settings, "email_allowlist", [])
    monkeypatch.setattr(settings, "email_nonprod_guard", False)
    assert email_mod.suppression_reason(OUTSIDER) is None
    assert email_mod.sending_disabled() is None
    assert await _send(OUTSIDER) is True
    assert [p["to"] for p in sent] == [[OUTSIDER]]


# ── THE FAIL-SAFE DEFAULT (guard ON) ──────────────────────────────────────────


@pytest.mark.parametrize("stage", ["dev", "staging", " DEV ", "Staging"])
async def test_dev_and_staging_with_no_allowlist_suppress_every_send(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """No allowlist means no mail on the two guarded stages — including to our own addresses.
    Case and surrounding whitespace do not sneak a stage off the list."""
    _stage(monkeypatch, stage, [])
    assert await _send() is False
    assert await _send(OUTSIDER) is False
    assert sent == []
    assert email_mod.stage_guard_suppresses_all() is True
    assert "EMAIL_ALLOWLIST is empty" in (email_mod.sending_disabled() or "")


@pytest.mark.parametrize("stage", ["test", "PRODUCTION_LIKE", "", "  ", "local"])
async def test_any_other_stage_mails_anyone_even_with_an_empty_allowlist(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """The guard is those two names, not "everything that is not production". A blank, a typo, or
    ``test`` mails freely — the same as production, which has its own test above."""
    _stage(monkeypatch, stage, [])
    assert await _send(OUTSIDER) is True
    assert [p["to"] for p in sent] == [[OUTSIDER]]
    assert email_mod.suppression_reason(OUTSIDER) is None
    assert email_mod.stage_guard_suppresses_all() is False
    assert email_mod.sending_disabled() is None


async def test_suppression_returns_false_and_never_raises(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """False is the same answer an unconfigured Resend gives, which is what every caller was already
    written against — no exception, whatever shape the recipient is."""
    _stage(monkeypatch, "dev", [])
    for recipient in (ADDRESS, OUTSIDER, "not-an-address", "", "nobody@"):
        assert await _send(recipient) is False
    assert sent == []


# ── what an allowlist admits ──────────────────────────────────────────────────


async def test_an_exact_address_admits_only_that_address(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _stage(monkeypatch, "staging", [BARE])
    assert await _send(BARE) is True
    assert await _send("someone.else@grapheneplatforms.com") is False
    assert await _send(OUTSIDER) is False
    assert [p["to"] for p in sent] == [[BARE]]
    assert email_mod.sending_disabled() is None
    assert email_mod.stage_guard_suppresses_all() is False


@pytest.mark.parametrize("entry", ["@grapheneplatforms.com", "grapheneplatforms.com"])
async def test_a_domain_entry_admits_the_whole_domain(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """Written with or without the ``@`` — both are the obvious thing to type, and neither should be
    the one that silently matches nothing."""
    _stage(monkeypatch, "dev", [entry])
    assert await _send("anyone@grapheneplatforms.com") is True
    assert await _send(OUTSIDER) is False
    assert len(sent) == 1


async def test_a_domain_entry_does_not_admit_subdomains_or_suffixes(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Domains match EXACTLY. ``@example.com`` quietly admitting ``@clients.example.com`` — or worse
    ``@notexample.com`` — is the kind of generosity that ends with a real client mailed from
    staging, and a suffix test is how it usually arrives."""
    _stage(monkeypatch, "dev", ["@grapheneplatforms.com"])
    for recipient in (
        "client@clients.grapheneplatforms.com",
        "client@notgrapheneplatforms.com",
        "client@grapheneplatforms.com.evil.example",
    ):
        assert await _send(recipient) is False
    assert sent == []


async def test_matching_tolerates_case_whitespace_and_a_display_name(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allowlist is typed by a human into a deploy UI and the recipient may arrive as a full
    ``Name <addr>`` mailbox. Neither is a reason to fail closed on a recipient that IS allowed — the
    guard has to be usable or it gets turned off."""
    _stage(monkeypatch, "dev", ["  Craig@GraphenePlatforms.COM  ", "", "   "])
    assert await _send("CRAIG@grapheneplatforms.com") is True
    assert await _send("  craig@grapheneplatforms.com  ") is True
    assert await _send(ADDRESS) is True
    assert len(sent) == 3
    assert email_mod.recipient_allowed(ADDRESS, [" @GraphenePlatforms.COM "]) is True


def test_a_recipient_that_is_not_address_shaped_matches_nothing() -> None:
    """ "Cannot be checked" resolves to "not allowed". Note the fifth case:
    ``@grapheneplatforms.com`` carries the allowed DOMAIN and no local part, so a matcher written
    on the domain alone admits it."""
    for recipient in ("", "   ", "craig", "<>", "@grapheneplatforms.com", "craig@"):
        assert email_mod.recipient_allowed(recipient, ["@grapheneplatforms.com", BARE]) is False


async def test_a_blank_allowlist_entry_cannot_admit_anyone(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whitespace is not configuration: a list of blanks is an empty list, not a wildcard."""
    _stage(monkeypatch, "dev", ["", " ", "\t"])
    assert await _send(OUTSIDER) is False
    assert await _send(BARE) is False
    assert sent == []
    assert email_mod.stage_guard_suppresses_all() is True


# (``EMAIL_ALLOWLIST``'s env parsing is pinned with the other list settings in test_config.py.)


# ── THE VALUE CHECKED IS THE VALUE SENT ───────────────────────────────────────

SMUGGLED = [
    "victim@evil.com, ok@grapheneplatforms.com",
    "Craig <craig@grapheneplatforms.com>, victim@evil.com",
    "victim@evil.com <craig@grapheneplatforms.com>",
    "victim@evil.com; craig@grapheneplatforms.com",
    "craig@grapheneplatforms.com\nbcc: victim@evil.com",
    "craig@grapheneplatforms.com\r\nvictim@evil.com",
]


@pytest.mark.parametrize("recipient", SMUGGLED)
def test_a_recipient_naming_more_than_one_mailbox_matches_nothing(recipient: str) -> None:
    assert email_mod.recipient_allowed(recipient, ["@grapheneplatforms.com", BARE]) is False


@pytest.mark.parametrize("recipient", SMUGGLED)
async def test_a_multi_mailbox_recipient_is_never_posted_even_in_production(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, recipient: str
) -> None:
    """The refusal is STAGE-INDEPENDENT, unlike the allowlist: production reads no allowlist at all,
    so a check living only inside ``suppression_reason`` would hand Resend the whole string — and a
    ``to`` naming two people is malformed data on every deployment."""
    for stage, allowlist in (("production", []), ("staging", ["@grapheneplatforms.com"])):
        _stage(monkeypatch, stage, allowlist)
        assert await _send(recipient) is False
    assert sent == []


async def test_the_address_posted_is_the_normalised_one_the_guard_checked(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive half, which makes the property structural rather than incidental: a display-name
    mailbox is admitted on its addr-spec, so the addr-spec is what goes on the wire."""
    _stage(monkeypatch, "staging", [BARE])
    assert await _send(ADDRESS) is True
    assert await _send("  CRAIG@GraphenePlatforms.com  ") is True
    assert [p["to"] for p in sent] == [[BARE], ["CRAIG@GraphenePlatforms.com"]]


async def test_a_malformed_recipient_puts_no_part_of_itself_in_the_log(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal has to be findable, but we could not decide what the string meant — so no part of
    it is printed, not even a "domain" (``rpartition`` on a two-address value returns the second
    one's host, which reads as if it were THE recipient's)."""
    _stage(monkeypatch, "production", [])
    with captured("app.email", level=logging.WARNING) as records:
        assert await _send("real.client@firm.co.uk, tester@grapheneplatforms.com") is False
    assert len(records) == 1
    text = _record_text(records[0])
    assert records[0].levelno >= logging.ERROR
    for leaked in ("real.client", "firm.co.uk", "tester", "grapheneplatforms.com"):
        assert leaked not in text, f"{leaked!r} reached the log"
    assert "EMAIL_ALLOWLIST" in text  # says why it could not be checked
    assert sent == []


# ── what reaches the log ──────────────────────────────────────────────────────


async def test_a_suppressed_send_logs_the_domain_and_never_the_address(
    sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loud enough for a developer to find (WARNING, with the reason and the fix), quiet enough to
    be safe in a log aggregator: the local part never appears."""
    _stage(monkeypatch, "dev", ["@grapheneplatforms.com"])
    with captured("app.email", level=logging.WARNING) as records:
        assert await _send(OUTSIDER) is False
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING
    text = _record_text(record)
    assert "someadviserfirm.co.uk" in text  # the domain, for debugging
    assert "a.real.client" not in text and OUTSIDER not in text  # never the person
    assert "EMAIL_ALLOWLIST" in text  # names the fix
    assert getattr(record, "event", None) == "email_suppressed"


async def test_an_unconfigured_send_also_logs_no_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pre-existing "RESEND_API_KEY not set" line logged the full address. Same rule, same
    chokepoint, so it is fixed here rather than left as the one leaky branch."""
    monkeypatch.setattr(settings, "resend_api_key", "")
    with captured("app.email", level=logging.WARNING) as records:
        assert await _send(OUTSIDER) is False
    text = _record_text(records[0])
    assert "someadviserfirm.co.uk" in text
    assert "a.real.client" not in text


async def test_the_resend_credential_never_reaches_the_log(
    failing_sends: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Driven through the FAILURE branch, the only place the credential exists.

    A non-production stage with an empty allowlist would suppress the send before the
    ``Authorization: Bearer`` header exists, so the token could not leak however the code were
    written — a vacuous pass, and this test had one (adding ``key=%s`` to ``send_email``'s
    ``logger.exception`` left the module green). The final assertions ARE the anti-vacuity guards:
    they fail if a refactor suppresses this send again."""
    _stage(monkeypatch, "production", [])
    with captured("app.email", level=logging.DEBUG) as records:
        assert await _send(OUTSIDER) is False
    assert all("re_test_key" not in _record_text(r) for r in records)
    assert len(failing_sends) == 1  # the payload was built, so the bearer header was too
    assert any(getattr(r, "event", None) == "email_send_failed" for r in records), (
        "the send was not attempted, so this test proves nothing about the credential"
    )


# ── THE TRIPWIRE — the guard is only a chokepoint while `send_email` is the only way out ──────

# `parents[1]`, not `.parent`: the transport lives in the `app/email/` package, so its own parent is
# that package and the scan below would cover the email feature alone — silently, since a narrower
# sweep still finds every module it looks at. `app/` is what has to be swept.
APP = Path(email_mod.__file__).resolve().parents[1]
SENDER_MODULE = APP / "email" / "transport.py"
CONFIG_MODULE = APP / "config.py"


def _app_sources() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def test_the_resend_credential_is_read_in_exactly_one_module() -> None:
    """``settings.resend_api_key`` outside ``app/email/transport.py`` means some other module can
    decide for
    itself whether to send. Health checks and the worker's loops ask ``email.configured()`` /
    ``email.sending_disabled()`` instead, so they read the DECISION and not the credential."""
    offenders = [
        str(path.relative_to(APP))
        for path in _app_sources()
        if path not in (SENDER_MODULE, CONFIG_MODULE)
        and "resend_api_key" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "These modules read the Resend credential directly: "
        f"{offenders}. Only app/email/transport.py may — every send funnels through its "
        "`send_email`, which is where the non-production allowlist guard lives "
        "(docs/api.md → Email). Ask "
        "`email.configured()` or `email.sending_disabled()` if you need to know whether mail can "
        "leave, and route the send itself through `email.send_email`."
    )


def test_nothing_in_the_api_imports_a_resend_sdk() -> None:
    """We send over plain HTTP from one function. An ``import resend`` anywhere would be a second
    client with its own configuration, its own defaults and no stage guard — and it would look
    entirely reasonable in review."""
    offenders: list[str] = []
    for path in _app_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name.split(".")[0] == "resend" for alias in node.names
            ):
                offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
            elif (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] == "resend"
                and node.level == 0
            ):
                offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert not offenders, (
        f"A Resend SDK is imported at {offenders}. Transactional email has exactly one door — "
        "`app.email.send_email` — because that is where the non-production recipient guard is "
        "enforced. Route the send through it instead."
    )


def test_the_resend_endpoint_is_named_in_exactly_one_module() -> None:
    """The URL is the other way to bypass the door: an ``httpx.post('https://api.resend.com/…')``
    needs no import worth noticing."""
    offenders = [
        str(path.relative_to(APP))
        for path in _app_sources()
        if path != SENDER_MODULE and "api.resend.com" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"These modules name Resend's API directly: {offenders}. Send through "
        "`app.email.send_email`, which applies the non-production allowlist guard first."
    )


def test_send_email_consults_the_guard_before_it_posts() -> None:
    """Pinned structurally, because a refactor that moved the check to the CALLERS (or dropped it
    while keeping the helper) would leave every behavioural test above passing on a function that no
    longer asks."""
    tree = ast.parse(SENDER_MODULE.read_text(encoding="utf-8"))
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "send_email"
    )
    calls = [
        node.func.id
        for node in ast.walk(func)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    posts = [
        node.lineno
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "post"
    ]
    assert "suppression_reason" in calls, (
        "app/email/transport.py::send_email no longer calls suppression_reason. That call IS the "
        "non-production send guard: without it, any deployment holding a RESEND_API_KEY mails real "
        "recipients (see this module's docstring)."
    )
    guard_line = next(
        node.lineno
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "suppression_reason"
    )
    assert posts and guard_line < min(posts), (
        "The suppression check must run BEFORE the HTTP POST to Resend. A guard that reads the "
        "allowlist after the mail has gone out is decoration."
    )


def test_send_email_records_every_outcome_it_can() -> None:
    """The ledger counterpart of the guard scan above, and pinned the same way and for the same
    reason: a refactor that moved recording out to the CALLERS would leave the behavioural tests in
    `test_comms_ledger.py` passing while real exit paths silently stopped producing a row — and a
    missing row is indistinguishable from an email that was never sent.

    Counted rather than matched pairwise, because the shape that must not change is "every way out
    of this function records, except the one that cannot". There are five ways out and four
    recordings; the gap is the malformed-recipient branch, which refuses precisely because it
    cannot say what the recipient string means and so cannot store it either.
    """
    tree = ast.parse(SENDER_MODULE.read_text(encoding="utf-8"))
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "send_email"
    )
    records = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record"
    ]
    returns = [node for node in ast.walk(func) if isinstance(node, ast.Return)]
    assert len(records) == 4, (
        f"app/email.py::send_email has {len(records)} ledger.record calls, expected 4 "
        "(unconfigured, suppressed, failed, sent). Every outcome of the chokepoint is recorded, "
        "including the ones where nothing was posted — 'we deliberately did not send this, and "
        "here is the setting that decided' is the answer a firm needs most often."
    )
    assert len(returns) == 5, (
        f"app/email.py::send_email has {len(returns)} exit points, expected 5. A NEW exit path "
        "must record before it returns; if it genuinely cannot (as the malformed-recipient branch "
        "cannot), say so in a comment there and update this count deliberately."
    )


def test_only_the_comms_package_writes_the_ledger() -> None:
    """`app/comms/ledger.py` is the only writer, exactly as `app/email.py` is the only sender.

    Reads are deliberately NOT restricted — the firm Activity feed and the event registry SELECT
    from these tables. What must stay in one place is the WRITE, because the status rule
    (`ledger.advance`) is only monotonic if every UPDATE goes through it; a second writer anywhere
    would be free to walk a delivered message backwards.
    """
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        if path.parent.name == "comms":
            continue
        body = path.read_text(encoding="utf-8")
        for verb in ("INSERT INTO comms.", "UPDATE comms.", "DELETE FROM comms."):
            if verb in body:
                offenders.append(f"{path.relative_to(APP)} ({verb.strip()})")
    assert not offenders, (
        "these modules write comms.* directly, bypassing app/comms/ledger.py: "
        + ", ".join(offenders)
        + ". Route the write through the ledger, whose `advance` rule is what keeps a delivery "
        "status from moving backwards under out-of-order provider callbacks."
    )


# ── reading a message back (the delivery fallback's half of the exchange) ─────────────────────


class _FakeReadClient:
    """One canned ``GET /emails/{id}`` answer per URL, plus the URLs it was actually asked for."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.urls: list[str] = []

    async def __aenter__(self) -> _FakeReadClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def get(self, url: str, **_kw: Any) -> Any:
        self.urls.append(url)
        answer = self.answers.get(url)
        if answer is None:
            return httpx.Response(404, request=httpx.Request("GET", url))
        if isinstance(answer, int):
            return httpx.Response(answer, request=httpx.Request("GET", url))
        return httpx.Response(200, json=answer, request=httpx.Request("GET", url))


def test_the_field_the_provider_answers_with_is_pinned() -> None:
    """`last_event` is READ OFF A REAL RESPONSE (03 Sep 2026), not assumed.

    CLAUDE.md's rule: verify external shapes, then pin them. If Resend renamed this field, every
    message would read as "the provider has no news" — a permanent silent no-op that no other test
    could tell from an ordinary quiet day.
    """
    real = {
        "object": "email",
        "id": "01a066f6-f4cf-76a1-841a-19b31fecf399",
        "to": ["someone@example.test"],
        "created_at": "2026-09-03 11:10:55.840000+00",
        "last_event": "delivered",
    }
    parsed = email_mod._last_event_of(real)
    assert parsed is not None and parsed.event == "delivered"


def test_the_list_reader_shares_the_pinned_extractor() -> None:
    """Both provider readers go through `_last_event_of`, so the pin above covers BOTH.

    `fetch_recent_events` (the list call) and `fetch_last_events` (per id) read the same
    `last_event` field, and the list path used to re-implement the three lines that read it. That
    made the test above cover half the code: a renamed field would be caught on the fallback path
    and missed on the one every healthy pass actually takes — the permanent silent no-op, arrived
    at from the other side.

    Asserted on the SOURCE, because the shape it forbids (a second `item.get("last_event")`) is
    behaviourally identical to the shape it wants until the day the field is renamed, and by then
    nobody is running this test.
    """
    import inspect

    body = inspect.getsource(email_mod.fetch_recent_events)
    assert "_last_event_of" in body, (
        "the list reader must go through the pinned extractor, not read `last_event` itself"
    )
    assert '"last_event"' not in body.split('"""', 2)[-1], (
        "the list reader names `last_event` in its own code — that is the duplicate extraction "
        "this test forbids (naming it in the docstring is fine)"
    )


def test_the_retrieval_timestamp_is_deliberately_not_carried() -> None:
    """`GET /emails/{id}` answers a `created_at`, and it is NOT the event's time.

    It is when the EMAIL RECORD was created. `ledger.record` stamps `sent_at` after the POST
    returns, so `created_at` is always the earlier of the two — using it as the occurrence of
    `last_event` wrote `delivered_at` BEFORE `sent_at`, and `timelineFor` sorts by instant, so a
    polled message rendered "Delivered 11:10:55 -> Sent 11:10:56". The retrieved event therefore
    carries the state alone, and `apply_provider_event`'s `now()` arm records when we LEARNED.
    """
    parsed = email_mod._last_event_of(
        {"last_event": "delivered", "created_at": "2026-09-03 11:10:55.840000+00"}
    )
    assert parsed is not None
    assert parsed.event == "delivered"
    assert not hasattr(parsed, "occurred_at")


async def test_the_message_id_cannot_escape_the_emails_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The id is provider-controlled text off `comms.messages`, and it lands in a URL PATH.

    Unescaped, a value containing `/` would reach a different Resend endpoint under our own bearer
    token — `../domains` being the obvious one. Percent-encoding keeps it one path segment.
    """
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    client = _FakeReadClient({})
    monkeypatch.setattr(email_mod.httpx, "AsyncClient", lambda **_kw: client)

    ids = ["../domains", "a/b", "ok?x=1"]
    await email_mod.fetch_last_events([*ids, "..", "  ."])
    for url, message_id in zip(client.urls, ids, strict=True):
        # The tail is EXACTLY one encoded segment — pinning the encoding itself rather than a
        # hand-rolled property, which is how the first version of this test passed `..%2Fdomains`
        # while claiming to reject it.
        assert url == f"{email_mod.RESEND_ENDPOINT}/{quote(message_id, safe='')}"
    # `quote` leaves dots alone, so a dot-only id would still traverse. It is refused outright,
    # which is why only the three real ids were requested.
    assert len(client.urls) == len(ids)


async def test_a_404_and_a_failure_are_absences_not_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A message pruned at the provider, one sent by another deployment sharing the account, and a
    provider blip all look alike — none may raise, and none may invent a status."""
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    base = email_mod.RESEND_ENDPOINT
    client = _FakeReadClient({f"{base}/good": {"last_event": "bounced"}, f"{base}/boom": 500})
    monkeypatch.setattr(email_mod.httpx, "AsyncClient", lambda **_kw: client)

    assert await email_mod.fetch_last_events(["good", "missing", "boom"]) == {
        "good": email_mod.RetrievedEmailEvent(event="bounced")
    }


async def test_nothing_is_asked_without_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "resend_api_key", "")
    client = _FakeReadClient({})
    monkeypatch.setattr(email_mod.httpx, "AsyncClient", lambda **_kw: client)

    assert await email_mod.fetch_last_events(["anything"]) == {}
    assert client.urls == []
