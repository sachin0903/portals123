"""UK National Insurance numbers, as HMRC issues them.

Two letters, six digits and a suffix letter A-D. Not every letter pair is a prefix: D, F, I, Q, U
and V are never used in either position, O is never the second letter, and BG, GB, KN, NK, NT, TN
and ZZ are not allocated. The one exception is ``QQ``, HMRC's own placeholder prefix
(``QQ 12 34 56 C`` in its guidance), accepted so that example and test data pass. The browser
applies the same rules (``pkgs/ui/src/lib/forms/national-insurance.ts``); this module is the
server-side half, used where a number is stored and where a fact find is submitted.
"""

from __future__ import annotations

import re

# The browser-side ``pattern`` for a form field: spaced or compact, any case.
FIELD_PATTERN = (
    r"^(?:[Qq][Qq]|(?![DFIQUVdfiquv])(?![A-Za-z][DFIOQUVdfioquv])"
    r"(?![Bb][Gg]|[Gg][Bb]|[Kk][Nn]|[Nn][Kk]|[Nn][Tt]|[Tt][Nn]|[Zz][Zz])"
    r"[A-Za-z]{2}) ?\d{2} ?\d{2} ?\d{2} ?[A-Da-d]$"
)
FIELD_MESSAGE = "Enter a valid National Insurance number, for example QQ 12 34 56 C"
IN_USE = "This National Insurance number is already in use."

_COMPACT = re.compile(
    r"^(?:QQ|(?![DFIQUV])(?![A-Z][DFIOQUV])(?!BG|GB|KN|NK|NT|TN|ZZ)[A-Z]{2})\d{6}[A-D]$"
)

#: How a UK National Insurance number is named. The fact find stores the regulator's own words
#: ("National Insurance Number"); a profile or an import may store the code.
_NINO_TYPES = frozenset({"nino", "national_insurance_number", "national insurance number"})


def normalise(value: object) -> str | None:
    """The stored form (``QQ123456C``) of a valid number, or None when it is not one."""
    if not isinstance(value, str):
        return None
    compact = re.sub(r"[\s-]", "", value).upper()
    return compact if _COMPACT.match(compact) else None


def is_valid(value: object) -> bool:
    return normalise(value) is not None


def is_nino_type(value: object) -> bool:
    """True when an identifier type is a UK National Insurance number."""
    return str(value or "").strip().casefold() in _NINO_TYPES


_BRITISH = frozenset({"united kingdom", "uk", "gb", "gbr", "british"})


def is_british(value: object) -> bool:
    """True when a nationality answer includes the United Kingdom.

    The fact find stores the country name. A profile stores the code. Either counts.
    """
    items = value if isinstance(value, list | tuple) else (value,)
    return any(str(item or "").strip().casefold() in _BRITISH for item in items)
