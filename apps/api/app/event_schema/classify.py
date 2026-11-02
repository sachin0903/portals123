"""What KIND of value a request field is — the one rule behind "can this event be replayed?".

An action event records the method, the route, the path params, the query and an allowlist of body
values, so "is the row enough to reconstruct the request" is a question about the body: every field
a caller could have sent is either captured, or a value we decided not to keep. This states that
decision once, beside the registry, so the replayability gate (`tests/test_event_replayability.py`)
reads it rather than mirroring it.

The categories, in the order they are decided:

``secret``    a credential. Never recorded, and refused twice — here, and by `actions.redact`.
``personal``  a person's own data: date of birth, gender, tax position, bank details. Closed
              values every one, and still not recorded as first-class subjects.
``structure`` an object or a list of them — a theme, a form's answers, a fee rule. Not a value but
              a document; recording it would be recording the body.
``prose``     free text. `registry._text` admits it only where the text IS the act.
``closed``    an enum, a bool, a number, a date, or an identifier. Recordable — and an uncaptured
              one is a gap rather than a decision.
"""

from __future__ import annotations

import datetime as dt
import enum
import re
import typing

#: A person's own data. See the module note — these are closed values we deliberately do not keep.
PERSONAL = frozenset(
    {
        "dob",
        "date_of_birth",
        "gender",
        "marital_status",
        "nationality",
        "national_identifier",
        "national_identifier_type",
        "tax_residency",
        "tax_relief",
        "us_person",
        "fatca_status",
        "fatca_certified_at",
        "employment_status",
        "employer_contributions",
        "relevant_uk_earnings",
        "retirement_age",
        "flexi_access_taken",
        "flexi_access_date",
        "occupation",
        "vulnerability_reasons",
        "sort_code",
        "account_number",
        "iban",
        "bic",
    }
)

SECRET = re.compile(r"password|secret|token|authorization|credential|api_key", re.I)

#: An id/code-shaped NAME. A bare `str` is prose unless it is named like an identifier — which is
#: what separates `code` (recordable) from `description` (not).
ID_NAME = re.compile(r"(^|_)(id|ids|uid|uids|code|slug|key|kind|type|status|ref)$")

Category = typing.Literal["secret", "personal", "structure", "prose", "closed"]


def category(name: str, annotation: object) -> Category:
    """Which kind of value this field is. See the module docstring for what each means."""
    if SECRET.search(name):
        return "secret"
    if name in PERSONAL:
        return "personal"

    origin = typing.get_origin(annotation)
    args = [a for a in typing.get_args(annotation) if a is not type(None)]

    if origin is typing.Literal:
        return "closed"
    if origin in (list, dict, set, tuple):
        return "structure"
    if args:
        # `X | None`, and other unions: the most permissive member decides, because a caller may
        # send any of them. `dict | None` is a structure however the null is spelled.
        seen = [category(name, a) for a in args]
        for worst in ("structure", "prose", "personal", "secret"):
            if worst in seen:
                return typing.cast(Category, worst)
        return "closed"
    if annotation in (bool, int, float, dt.date, dt.datetime):
        return "closed"
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return "closed"
    if annotation is str:
        return "closed" if ID_NAME.search(name) else "prose"
    # A pydantic model, `Any`, or something exotic: a document, not a value.
    return "structure"


def recordable(name: str, annotation: object) -> bool:
    """Could the trail record this value? True exactly for `closed`."""
    return category(name, annotation) == "closed"
