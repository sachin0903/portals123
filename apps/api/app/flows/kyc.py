"""KYC dedup + validity — the person-scoped rules (pure, unit-testable).

KYC is a property of the contact: a ``passed`` check is reused across accounts/applications (no
duplicate identity/AML — the user's requirement). The store is ``crm.kyc_checks`` (0026);
``ReviewHandler`` (verify_identity / aml_check) applies these rules — ``pick_valid`` IS its dedup
decision and ``compute_valid_until`` IS the horizon it stamps, so there is no second copy of either
in SQL.

An identity or AML pass does not lapse: it stands until a later check replaces it. Ongoing AML
monitoring and the re-screen on a change of name, date of birth or nationality
(``compliance.mark_screen_stale``) keep a screen current, not a timer. Only a terms acceptance has
a horizon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

KIND_IDENTITY = "identity"
KIND_AML = "aml"
KIND_TERMS = "terms"

#: How long a pass of each kind that lapses stays valid. Identity and AML are not here: they do not
#: lapse. Terms do not expire on a screening horizon either — they are a recorded acknowledgement —
#: so their horizon is long enough that Compliance keeps reading as accepted until a later journey
#: replaces the row.
LAPSE_DAYS: dict[str, int] = {
    KIND_TERMS: 3650,
}


@dataclass(frozen=True)
class KycCheck:
    contact_id: int
    kind: str
    status: str
    verified_at: datetime | None = None
    valid_until: datetime | None = None


def is_valid(check: KycCheck, now: datetime) -> bool:
    """A check counts as a valid, reusable pass: ``passed``, and not past its horizon when it has
    one. A pass with no horizon (identity, AML) stands."""
    return check.status == "passed" and (check.valid_until is None or check.valid_until > now)


def pick_valid(checks: list[KycCheck], kind: str, now: datetime) -> KycCheck | None:
    """The furthest-valid reusable check of ``kind`` for a contact (dedup short-circuit source): a
    pass with no horizon first, else the one valid longest."""
    candidates = [c for c in checks if c.kind == kind and is_valid(c, now)]
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c.valid_until is None, c.valid_until or now))


def compute_valid_until(verified_at: datetime, kind: str) -> datetime | None:
    """The horizon a fresh pass is valid until, or None for a kind that does not lapse."""
    days = LAPSE_DAYS.get(kind)
    return verified_at + timedelta(days=days) if days is not None else None
