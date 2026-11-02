"""The notification click-target logic (``consumer._click_link``) — pure.

Fixes the "clicking a notification does nothing but mark it read" bug: a staff notice with no
explicit link now deep-links to the subject's record, so a click lands somewhere useful. An explicit
rule link always wins; a client-portal notice with no link stays link-less on purpose (the client
portal is composed and has no ``/clients/{uid}`` record view).
"""

from __future__ import annotations

from app.notifications.consumer import _click_link


def test_explicit_link_always_wins() -> None:
    assert _click_link("/tasks", "platform", "contact", "con_abc123xyza") == "/tasks"
    assert _click_link("/documents", "client", "contact", "con_abc123xyza") == "/documents"


def test_staff_notice_deep_links_to_the_subject() -> None:
    assert _click_link(None, "platform", "contact", "con_abc123xyza") == "/clients/con_abc123xyza"
    assert _click_link(None, "platform", "account", "acc_abc123xyza") == "/accounts/acc_abc123xyza"


def test_client_notice_without_a_link_stays_linkless() -> None:
    # No /clients/{uid} record view in the composed client portal.
    assert _click_link(None, "client", "contact", "con_abc123xyza") is None


def test_no_subject_yields_no_link() -> None:
    assert _click_link(None, "platform", None, None) is None
    assert _click_link(None, "platform", "contact", None) is None


# ---- the portal a notification is stamped for -------------------------------------------------
# `recipient_portal` exists because `catalogue.audience_portal` reads the audience KEY, which is
# right for every audience whose resolver can only find staff and wrong for the one that can find
# either. See the function's own docstring for the row this shipped.

from app.notifications.consumer import recipient_portal  # noqa: E402
from app.notifications.registry import Recipient  # noqa: E402

_STAFF = Recipient(kind="user", user_id="u-1")
_CLIENT = Recipient(kind="contact", contact_id=7)


def test_a_client_recipient_is_stamped_for_the_client_portal() -> None:
    """THE regression. `task.assigned` is authored under a staff audience ('assignee'), so the rule
    says 'platform' — but the assignee resolved to a contact, and a client holds no staff portal."""
    assert recipient_portal("platform", [_CLIENT]) == "client"


def test_a_staff_recipient_keeps_the_rules_portal() -> None:
    assert recipient_portal("platform", [_STAFF]) == "platform"
    # A client-audience rule is already right and must not be second-guessed.
    assert recipient_portal("client", [_CLIENT]) == "client"


def test_a_mixed_list_keeps_the_authors_portal() -> None:
    """No resolver fans one notice to staff and clients together, and if one ever did, guessing a
    single portal for both would be worse than keeping the one the rule was authored with."""
    assert recipient_portal("platform", [_STAFF, _CLIENT]) == "platform"


def test_no_recipients_changes_nothing() -> None:
    # `all()` is vacuously true on an empty list — which would have flipped every empty fan-out to
    # the client portal.
    assert recipient_portal("platform", []) == "platform"
