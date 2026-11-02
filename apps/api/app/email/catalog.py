"""Catalogue of the email codes the send path actually resolves.

A firm's email template only DOES anything when its ``code`` matches one a send site looks up via
``email_render.render_or_default(code=...)``. This is the curated list of those codes — the source
the authoring UI offers as selectable "email types", so a firm picks a real trigger rather than
guessing a free-text code that would silently never send.

Keep in step with the send sites (grep ``render_or_default(`` — ``provisioning.py``,
``routers/internal.py``, ``documents/service.py``, ``tasks/notifier.py``).
``tests/test_email/catalog.py`` fails if a code here is not actually used, or a used code is
missing here.

A code is RETIRED by dropping it from this list, never by deleting a firm's rows: ``document_share``
went that way when the document share-links feature was removed. A firm that published a template
against it keeps the row (it stays listed, readable and editable), but the code is no longer
offerable on either catalogue endpoint and no send site resolves it, so nothing renders it.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

Category = Literal["transactional", "notification"]

#: The general wrapper's own CODE — the branded layout every in-app notification's email renders
#: through, and the template a notification rule falls back to when it names none.
#:
#: Spelled the same as the `notification` CATEGORY and meaning something else entirely: the category
#: groups seven codes, this names one of them. It is also the null-sentinel on the send path
#: (`email_consumer`: `rule.email_code or WRAPPER_CODE`), so one string carries three jobs — hence a
#: name for the one that is a fallback, which is the job a bare literal cannot advertise.
WRAPPER_CODE = "notification"


class EmailCode(NamedTuple):
    code: str
    label: str
    category: Category
    description: str


# Ordered for display: transactional (account lifecycle) first, then client notifications.
KNOWN_CODES: list[EmailCode] = [
    EmailCode(
        "invite",
        "Invitation",
        "transactional",
        "Sent when someone is invited or granted portal access — carries the set-password link.",
    ),
    EmailCode(
        "password_reset",
        "Password reset",
        "transactional",
        "Sent when a user requests a password reset — carries the reset link.",
    ),
    EmailCode(
        "document_sent",
        "Document sent",
        "notification",
        "Sent to a client when you send them a document to view.",
    ),
    EmailCode(
        "request_raised",
        "Document request",
        "notification",
        "Sent to a client when you request documents or information from them.",
    ),
    # TWO CODES FOR ONE FEATURE, deliberately. There was one — ``task_notice`` — and it was really
    # two emails behind an ``is_reminder`` branch: its heading and its lead sentence each chose
    # between "New task"/"You have been assigned a task." and "Reminder"/"This task is due soon.".
    # Conditions are a capability worth supporting, but a SHIPPED DEFAULT must not lean on them: the
    # canvas an author opens has to be the email, not the email plus the other email plus the rule
    # that picks. Splitting also gives a firm the thing one template could not — reword the reminder
    # without touching the assignment notice, which are different sends to different people at
    # different times.
    #
    # ``task_notice`` is RETIRED by its absence from this list (see the module docstring): a firm
    # that published one keeps the row, listed and editable, and nothing resolves it any more.
    EmailCode(
        "task_assigned",
        "Task assigned",
        "notification",
        "Sent to a staff member or client when a task is assigned to them.",
    ),
    EmailCode(
        "task_reminder",
        "Task reminder",
        "notification",
        "Sent to a staff member or client when a task assigned to them is nearly due.",
    ),
    EmailCode(
        WRAPPER_CODE,
        "General notification",
        "notification",
        "The single wrapper for every in-app notification's email — the branded layout each "
        "notification's own title, message and link are dropped into. Edit it once to restyle them "
        "all.",
    ),
]


# The email templates a NOTIFICATION RULE may render — the builder's "Email template" dropdown.
# Every notification-category code, the general ``notification`` wrapper included (it is the default
# for a rule with no explicit choice). This used to carry one exclusion — ``document_share``, a
# secure-link share to an EXTERNAL recipient rather than a rule-driven notification — which went
# when that feature was removed, so the category alone is the rule again.
def notification_email_options() -> list[EmailCode]:
    """The email codes selectable as a notification rule's template (code + label for the menu)."""
    return [c for c in KNOWN_CODES if c.category == "notification"]


def notification_email_codes() -> set[str]:
    """The set of codes a notification rule's ``email_code`` may take (the validation allowlist)."""
    return {c.code for c in notification_email_options()}
