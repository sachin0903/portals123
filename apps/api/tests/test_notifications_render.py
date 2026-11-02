"""The notification copy renderer (``app/notifications/render.py``) — pure, no DB.

Pins the two behaviours the feature leans on: tokens resolve from a nested context, and an
UNRESOLVED token is dropped with its whitespace tidied (never left as a raw ``{token}``), so a
firm's authored copy degrades gracefully when the event does not carry a field.
"""

from __future__ import annotations

from app.notifications.render import render

_CTX = {
    "client": {"name": "Jane Doe"},
    "actor": {"name": "Sarah Adviser"},
    "firm": {"name": "Oakview Wealth"},
    "account": {"name": "ISA", "number": "ISA-000123"},
}


def test_resolves_dotted_tokens() -> None:
    assert (
        render("{actor.name} shared a document with {client.name}", _CTX)
        == "Sarah Adviser shared a document with Jane Doe"
    )


def test_resolves_account_fields() -> None:
    assert render("Model changed on {account.name} ({account.number})", _CTX) == (
        "Model changed on ISA (ISA-000123)"
    )


def test_unresolved_token_is_dropped_and_whitespace_tidied() -> None:
    # No document.name in context → the token and the doubled space it leaves are removed.
    assert render("A document {document.name} was shared", _CTX) == "A document was shared"


def test_unresolved_leading_token_trims() -> None:
    assert render("{missing.thing} uploaded a file", _CTX) == "uploaded a file"


def test_no_placeholders_is_unchanged() -> None:
    assert render("A document was shared with you", _CTX) == "A document was shared with you"


def test_empty_template_is_unchanged() -> None:
    assert render("", _CTX) == ""


def test_newlines_preserved_in_body() -> None:
    out = render("Hi {client.name},\nYour account {account.name} changed.", _CTX)
    assert out == "Hi Jane Doe,\nYour account ISA changed."


def test_non_token_braces_left_untouched() -> None:
    # A brace group that is not a valid dotted-lowercase path is not a placeholder.
    assert render("Use {Not A Token} literally", _CTX) == "Use {Not A Token} literally"


def test_dict_valued_path_is_not_substituted() -> None:
    # Resolving to a nested object (not a scalar leaf) yields no value → dropped.
    assert render("{client} here", _CTX) == "here"


def test_missing_context_key_drops_token() -> None:
    assert render("{firm.name}: {client.name}", {"firm": {"name": "Acme"}}) == "Acme:"
