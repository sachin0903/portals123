"""The designated-action catalogue — the book actions a firm can hold for approval.

Platform-owned, not firm-invented: a friendly key, a human label, a family, a wave. A firm
never types an event name. The settings page lists WIRED actions only (a toggle that does
nothing is worse than a short list). Later waves add an entry here plus apply/before/describe
in ``actions.py`` — no new engine.

The keys are a public contract the way automation trigger keys are: stored on
``actions.policies`` / ``actions.runs``, so renaming one is a migration.
"""

from __future__ import annotations

from dataclasses import dataclass

FAMILIES: frozenset[str] = frozenset({"money", "contributions", "identity"})
SUBJECT_KINDS: frozenset[str] = frozenset({"contact", "account", "instruction"})
WAVES: frozenset[int] = frozenset({1, 2, 3})


@dataclass(frozen=True)
class ActionSpec:
    """One designated action a firm may put behind a four-eye check."""

    key: str
    label: str
    description: str
    family: str
    event_type: str
    subject_kind: str
    wave: int
    wired: bool
    sensitivity: str = "normal"  # "redact" = payload stays off audit.actions
    #: One open hold per subject (the default): a second proposal while one waits is refused with
    #: the open case named. Off for an action whose one key covers several independent cards —
    #: each hold snapshots only its own fields, so disjoint edits can wait side by side and an
    #: overlapping one is what gets refused.
    one_open: bool = True


def _a(
    key: str,
    label: str,
    description: str,
    family: str,
    event_type: str,
    subject_kind: str,
    wave: int,
    *,
    wired: bool = False,
    sensitivity: str = "normal",
    one_open: bool = True,
) -> ActionSpec:
    return ActionSpec(
        key=key,
        label=label,
        description=description,
        family=family,
        event_type=event_type,
        subject_kind=subject_kind,
        wave=wave,
        wired=wired,
        sensitivity=sensitivity,
        one_open=one_open,
    )


ACTIONS: tuple[ActionSpec, ...] = (
    # -- wave 1: money movement, already split doors + instructions:write ----------------------
    _a(
        "contact.bank_details_changed",
        "Update client bank account",
        "Write the bank account on the client. Approvers on Update client bank account hold this.",
        "money",
        "client.bank_account_updated",
        "contact",
        1,
        wired=True,
        sensitivity="redact",
    ),
    _a(
        "account.bank_details_changed",
        "Update account bank details",
        "Write the account's own bank account. Approvers on Update account bank details hold this.",
        "money",
        "account.bank_accounts_updated",
        "account",
        1,
        wired=True,
        sensitivity="redact",
    ),
    _a(
        "account.model_changed",
        "Change model",
        "A second person must approve before an account's investment model changes.",
        "money",
        "account.model_updated",
        "account",
        1,
        wired=True,
    ),
    # -- wave 2: money-adjacent ---------------------------------------------------------------
    _a(
        "account.fee_packages_changed",
        "Change fee packages",
        "A second person must approve before an account's fee packages change.",
        "money",
        "account.fee_packages_updated",
        "account",
        2,
        wired=True,
    ),
    _a(
        "contribution.created",
        "Book a contribution",
        "A second person must approve before a contribution is booked.",
        "contributions",
        "contribution.created",
        "account",
        2,
    ),
    _a(
        "contribution.cancelled",
        "Cancel a contribution",
        "A second person must approve before a contribution is cancelled.",
        "contributions",
        "contribution.cancelled",
        "account",
        2,
    ),
    _a(
        "contribution.categorised",
        "Categorise a contribution",
        "A second person must approve before a contribution is classified.",
        "contributions",
        "contribution.categorised",
        "account",
        2,
    ),
    _a(
        "contribution.expectation_lodged",
        "Lodge a contribution expectation",
        "A second person must approve before a custodian is told to expect money.",
        "contributions",
        "contribution.expectation_lodged",
        "account",
        2,
    ),
    _a(
        "transfer.created",
        "Raise a transfer in",
        "A second person must approve before a transfer in is raised.",
        "contributions",
        "transfer.created",
        "account",
        2,
    ),
    _a(
        "account.beneficiary_added",
        "Add a beneficiary nomination",
        "A second person must approve before a beneficiary is nominated.",
        "identity",
        "account.beneficiary_added",
        "account",
        2,
    ),
    _a(
        "account.beneficiary_removed",
        "Remove a beneficiary nomination",
        "A second person must approve before a beneficiary nomination is withdrawn.",
        "identity",
        "account.beneficiary_removed",
        "account",
        2,
    ),
    # -- wave 3: identity / access, catalogue only --------------------------------------------
    _a(
        "client.archived",
        "Close a client",
        "A second person must approve before a client is closed.",
        "identity",
        "client.archived",
        "contact",
        3,
    ),
    _a(
        "client.restored",
        "Reopen a client",
        "A second person must approve before a closed client is reopened.",
        "identity",
        "client.restored",
        "contact",
        3,
    ),
    _a(
        "client.adviser_assigned",
        "Assign an adviser",
        "A second person must approve before an adviser is assigned to a client.",
        "identity",
        "client.adviser_assigned",
        "contact",
        3,
    ),
    _a(
        "client.adviser_unassigned",
        "Unassign an adviser",
        "A second person must approve before an adviser is removed from a client.",
        "identity",
        "client.adviser_unassigned",
        "contact",
        3,
    ),
    _a(
        "client.portal_user_linked",
        "Link a client login",
        "A second person must approve before a portal login is linked to a client.",
        "identity",
        "client.portal_user_linked",
        "contact",
        3,
    ),
    _a(
        "client.portal_user_unlinked",
        "Unlink a client login",
        "A second person must approve before a portal login is unlinked from a client.",
        "identity",
        "client.portal_user_unlinked",
        "contact",
        3,
    ),
    _a(
        "client.portal_invited",
        "Invite a client to the portal",
        "A second person must approve before a client is invited to the portal.",
        "identity",
        "client.portal_invited",
        "contact",
        3,
    ),
    _a(
        "client.vulnerability_updated",
        "Update a client's vulnerability record",
        "A second person must approve before a vulnerability record is changed.",
        "identity",
        "client.vulnerability_updated",
        "contact",
        3,
        wired=True,
    ),
    # One key, five cards (contact, personal, pension, employer, compliance): a hold on the
    # pension card must not stop the contact card being edited, so holds are per field set and
    # only an edit to a field already waiting is refused.
    _a(
        "client.details_changed",
        "Edit client details",
        "A second person must approve before a client's details are edited.",
        "identity",
        "client.details_updated",
        "contact",
        3,
        wired=True,
        one_open=False,
    ),
)

ACTIONS_BY_KEY: dict[str, ActionSpec] = {a.key: a for a in ACTIONS}
WIRED: tuple[ActionSpec, ...] = tuple(a for a in ACTIONS if a.wired)
WIRED_BY_KEY: dict[str, ActionSpec] = {a.key: a for a in WIRED}

#: Keys a firm could once hold that left the catalogue on 22 Oct 2026. A money instruction is
#: held at its SEND (approvers on `transaction.payment` / `withdrawal` / `fee`), so a review of
#: the booking was a second person approving the same payment twice; a cancel is immediate; a
#: standing schedule is `payment.regular` / `withdrawal.regular`; a nomination is
#: `beneficiary.replace`. Settled history still carries these keys, so the queue can name them.
RETIRED: dict[str, str] = {
    "instruction.raised": "Raise a money-movement instruction",
    "instruction.cancelled": "Cancel a money-movement instruction",
    "account.regular_movements_changed": "Change an account's regular movements",
    "account.beneficiaries_changed": "Change beneficiary nominations",
}


def get(key: str) -> ActionSpec | None:
    return ACTIONS_BY_KEY.get(key)


def require_wired(key: str) -> ActionSpec:
    spec = WIRED_BY_KEY.get(key)
    if spec is None:
        raise KeyError(f"Unknown or unwired review action '{key}'")
    return spec
