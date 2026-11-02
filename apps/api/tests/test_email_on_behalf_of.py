"""Sending AS AN ADVISER, not as the firm.

A notice that reads "Acme Wealth" is right for a statement and wrong for the email an adviser writes
to their own client: the client's relationship is with a person, and a reply that lands in a support
inbox is a reply that person never sees.

The constraint is Resend's, not ours. It refuses an unverified sending domain, so the adviser's own
address can only BE the ``From`` where it sits on the verified domain. Everywhere else the verified
address stays and the envelope carries the adviser's name plus the firm's, with the adviser's
address
as ``Reply-To`` — which is as far as this can honestly go, and is still a notice that reads as
coming
from a person and replies to them.

The capability exists here and no send site uses it yet; wiring one is a separate decision per site.
"""

from __future__ import annotations

import pytest

from app.email import transport as email_mod

VERIFIED = "Graphene <no-reply@graphene.test>"


def _firm(monkeypatch: pytest.MonkeyPatch, *, name: str, support: str) -> None:
    """Stand in for the tenant read, the way `test_documents_api` does."""

    async def _row(session, tenant_id):  # noqa: ANN001, ARG001
        return name, {"client": {"support": {"email": support}}}

    monkeypatch.setattr(email_mod, "_tenant_row", _row)


@pytest.fixture(autouse=True)
def _cold_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    email_mod.clear_caches()
    monkeypatch.setattr(email_mod.settings, "email_from", VERIFIED)


@pytest.mark.asyncio
async def test_an_adviser_on_the_verified_domain_is_the_literal_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The honest header, available only here: the address in the From is the one that will receive
    a reply, so there is nothing left for Reply-To to say."""
    _firm(monkeypatch, name="Ridley & Co", support="hello@ridley.test")
    sender = await email_mod.resolve_sender(
        None,  # type: ignore[arg-type]
        7,
        on_behalf_of=email_mod.OnBehalfOf("Sarah Whitfield", "sarah@graphene.test"),
    )
    assert sender.from_addr == '"Sarah Whitfield" <sarah@graphene.test>'
    assert sender.reply_to is None


@pytest.mark.asyncio
async def test_an_adviser_off_the_domain_keeps_the_verified_address_and_replies_to_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both names in the display: the firm is who the client has a relationship with, the adviser is
    who they are hearing from, and either alone answers half the question. Sending from
    `sarah@ridley.test` here would be refused by Resend, so the notice would simply not arrive."""
    _firm(monkeypatch, name="Ridley & Co", support="hello@ridley.test")
    sender = await email_mod.resolve_sender(
        None,  # type: ignore[arg-type]
        7,
        on_behalf_of=email_mod.OnBehalfOf("Sarah Whitfield", "sarah@ridley.test"),
    )
    assert sender.from_addr == '"Sarah Whitfield (Ridley & Co)" <no-reply@graphene.test>'
    assert sender.reply_to == "sarah@ridley.test"


@pytest.mark.asyncio
async def test_an_adviser_with_no_address_still_names_them_and_replies_to_the_firm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An adviser record may hold a name and no email. Degrading to the firm's support address is
    right: a reply reaching a colleague beats a reply reaching nobody, and refusing the send would
    lose the notice entirely over a field the client never sees."""
    _firm(monkeypatch, name="Ridley & Co", support="hello@ridley.test")
    sender = await email_mod.resolve_sender(
        None,  # type: ignore[arg-type]
        7,
        on_behalf_of=email_mod.OnBehalfOf("Sarah Whitfield"),
    )
    assert sender.from_addr == '"Sarah Whitfield (Ridley & Co)" <no-reply@graphene.test>'
    assert sender.reply_to == "hello@ridley.test"


@pytest.mark.asyncio
async def test_the_adviser_envelope_never_reaches_the_firms_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE HAZARD THIS GUARDS. `_sender_cache` is keyed by tenant and portal only, so an adviser's
    envelope stored under it would be handed to the next caller for that firm — every subsequent
    email from that tenant going out as one adviser. It is the `sender_for` failure in reverse, and
    silent in the same way, so the person path resolves fresh and stores nothing."""
    _firm(monkeypatch, name="Ridley & Co", support="hello@ridley.test")
    await email_mod.resolve_sender(
        None,  # type: ignore[arg-type]
        7,
        on_behalf_of=email_mod.OnBehalfOf("Sarah Whitfield", "sarah@graphene.test"),
    )
    assert email_mod._sender_cache == {}
    # And the firm's own resolution is untouched by it.
    firm = await email_mod.resolve_sender(None, 7)  # type: ignore[arg-type]
    assert firm.from_addr == '"Ridley & Co" <no-reply@graphene.test>'
    assert firm.reply_to == "hello@ridley.test"


@pytest.mark.asyncio
async def test_omitting_it_is_byte_identical_to_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """The parameter is additive. Every existing send site passes nothing and must be unaffected —
    including the cache write, which the firm path still depends on for `sender_for`."""
    _firm(monkeypatch, name="Ridley & Co", support="advice@graphene.test")
    sender = await email_mod.resolve_sender(None, 8)  # type: ignore[arg-type]
    assert sender.from_addr == '"Ridley & Co" <advice@graphene.test>'
    assert sender.reply_to is None
    assert email_mod.sender_for(8) == sender.from_addr


@pytest.mark.asyncio
async def test_a_display_name_cannot_split_the_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_mailbox` escapes the quoted string, and an adviser name is now one of the values that reach
    it — a name holding a quote or a comma would otherwise turn one From into two addresses."""
    _firm(monkeypatch, name="Ridley & Co", support="hello@ridley.test")
    sender = await email_mod.resolve_sender(
        None,  # type: ignore[arg-type]
        7,
        on_behalf_of=email_mod.OnBehalfOf('Sarah "Sam" Whitfield, CFP', "sarah@ridley.test"),
    )
    # ONE address, and the inner quotes stripped rather than escaped-and-kept: the display name is
    # wrapped in a quoted string, so a quote inside it would close that string early and everything
    # after the comma would parse as a second recipient.
    assert sender.from_addr == '"Sarah Sam Whitfield, CFP (Ridley & Co)" <no-reply@graphene.test>'
    assert sender.from_addr.count("<") == 1
