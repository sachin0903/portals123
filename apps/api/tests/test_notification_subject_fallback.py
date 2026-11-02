"""A notification records what it is ABOUT, even when the flows registry cannot say.

THE DEFECT. ``consumer`` asks ``catalogue._subject_of`` for the subject, and that answers from the
FLOWS trigger registry. Not every event that notifies has an entry there — chat messages are the
standing example: there is no flows trigger whose event is ``message.sent``, so every message
notification was stored with ``subject_type``/``subject_id`` NULL, although its own notification
Trigger declares ``subject_kind="contact"`` and the event carries the contact on its allowlisted
subject.

Why that matters more than it looks. The subject is the row's answer to "which client is this
about", so with it null nothing downstream can tell one client's message notice from another's. The
reader that needed it most was the platform portal's "am I already looking at this thread?"
suppression, which compares the notification's subject against the thread on screen — it matched
nothing at all, silently, and the symptom (a toast about a message you are watching arrive) is
identical to the feature not being built.

These are unit tests over the two pure pieces because the failure was in the DECISION, not in the
delivery: the consumer wrote a row perfectly well, it just wrote a null into it.
"""

from __future__ import annotations

from typing import Any

from app.notifications.consumer import _click_link
from app.notifications.context import _fallback_contact_id


def _message_action(**over: Any) -> dict[str, Any]:
    """A real ``message.sent`` row, shaped as `audit.actions` stores it (verified against the
    running dev database: the producer puts `contact_id` on the allowlisted subject)."""
    action: dict[str, Any] = {
        "event_type": "message.sent",
        "metadata": {
            "query": {"contact_id": "8"},
            "subject": {"contact_id": 8, "conversation_adviser_id": 1},
            "correlation_id": "8545b13a8b1f4c3687f54c6a66f52dec",
        },
    }
    action.update(over)
    return action


def test_a_message_event_names_its_contact() -> None:
    # The whole fallback rests on this: the producer already records the contact, so the consumer
    # never has to guess or look one up.
    assert _fallback_contact_id(_message_action()) == 8


def test_an_attachment_send_names_it_too() -> None:
    assert _fallback_contact_id(_message_action(event_type="message.attachment_sent")) == 8


def test_an_event_with_no_contact_yields_nothing() -> None:
    # Not every event is about a client. The fallback must stay silent rather than inventing a
    # subject, or an unrelated notice would deep-link to a client it has nothing to do with.
    assert (
        _fallback_contact_id({"event_type": "tenant.created", "metadata": {"subject": {}}}) is None
    )
    assert _fallback_contact_id({"event_type": "x", "metadata": {}}) is None


def test_a_malformed_contact_is_refused_not_coerced() -> None:
    bad = _message_action()
    bad["metadata"]["subject"] = {"contact_id": "not-a-number"}
    assert _fallback_contact_id(bad) is None


def test_populating_the_subject_does_not_move_the_link() -> None:
    # The reason this fallback is safe to apply to every family at once. `_click_link` invents a
    # target ONLY when the rule ships none, and every message trigger ships `/chats` — so a
    # notification that gains a subject keeps the link it always had. Were that not true, filling
    # the subject in would silently re-point the whole messages family at `/clients/{uid}`.
    assert _click_link("/chats", "platform", "contact", "8") == "/chats"
    assert _click_link("/adviser/messages", "client", "contact", "8") == "/adviser/messages"


def test_a_subjectless_staff_notice_still_deep_links_when_it_gains_one() -> None:
    # The other half: a rule with NO link of its own is exactly the case the fallback improves —
    # before it, such a notice had no subject and so no target at all.
    assert _click_link(None, "platform", "contact", "8") == "/clients/8"
    assert _click_link(None, "platform", None, None) is None
