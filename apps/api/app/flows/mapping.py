"""The data-gathering write-back mapper — turn gathered form datapoints into real CRM fields.

The user's requirement: the profile stage's datapoints *become the client on the CRM*, not just a
stored form response. Per the review, transformation is a **pipeline** (source → normalise →
validate → target), so logic like postcode-normalisation or date-locale conversion lives here, not
baked into field definitions.

A ``fill_form`` step in data-gathering mode carries a ``mapping`` in its config: a list of
``{source, target, transforms?, required?}``. ``apply_mapping`` runs it against the client's answers
and returns the ``{crm_column: value}`` patch to write to ``crm.contacts`` (+ any validation
errors). Pure + unit-tested; the forms handler does the actual UPDATE.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

# ---- transforms: pure str -> str, composed left to right ----
_WS = re.compile(r"\s+")
_NON_DIGIT = re.compile(r"\D")
_DATE_DMY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
# Whitespace-only transforms: a multiSelect answer may carry these without forcing a join-to-str.
_TRIM_LIKE = frozenset({"trim", "collapse_spaces"})


def _iso_date(v: str) -> str:
    """Normalise ``DD/MM/YYYY`` (UK) or an already-ISO date to ``YYYY-MM-DD``; pass anything else
    through untouched (validation, if required, catches genuinely bad values downstream)."""
    if _DATE_ISO.match(v):
        return v
    m = _DATE_DMY.match(v)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return v


def _postcode(v: str) -> str:
    """UK postcode: upper-case, collapse internal whitespace to one space, trim."""
    return _WS.sub(" ", v.upper()).strip()


def _snake(v: str) -> str:
    """A display label to its canonical stored value: "Self-Employed" -> "self_employed".

    The forms show a person a label and the columns hold a canonical value — `marital_status` and
    `employment_status` are plain `text`, but everything that READS them (the client details form's
    `Literal`s, the Morgan Lloyd boundary mapping) expects the snake_case member. Writing the label
    straight through would store a value nothing downstream recognises, and `text` means the
    database would not complain.
    """
    return _WS.sub("_", re.sub(r"[^0-9a-z]+", " ", v.lower()).strip())


TRANSFORMS: dict[str, Callable[[str], str]] = {
    "snake": _snake,
    "trim": str.strip,
    "upper": str.upper,
    "lower": str.lower,
    "title": str.title,
    "collapse_spaces": lambda v: _WS.sub(" ", v).strip(),
    "digits": lambda v: _NON_DIGIT.sub("", v),
    "iso_date": _iso_date,
    "postcode": _postcode,
}


class MappingError(ValueError):
    """An authored mapping is malformed (unknown transform / target). Surfaces at publish."""


@dataclass(frozen=True)
class FieldMapping:
    source: str  # the form field_key
    target: str  # the crm.contacts column (or dotted path into a jsonb column)
    transforms: tuple[str, ...] = ()
    required: bool = False

    def run(self, raw: object) -> str | list[str] | list[dict[str, object]] | bool | None:
        """Apply the transform pipeline to one answer.

        Non-str/absent → None (unless required, which the caller validates), except a list/tuple
        with no transforms (or only trim-like ones): those pass through as a list of stripped
        non-empty strings — nationality is a multiSelect and must not be ``str()``'d into one value.
        """
        if raw is None:
            return None
        # A yes/no declaration is a real boolean fact. Stringifying False to "False" makes it
        # truthy to every later JSON consumer and loses the difference this field exists to record.
        if isinstance(raw, bool) and not self.transforms:
            return raw
        # A list of OBJECTS is a structured answer — the `phoneNumbers` editor's
        # `[{"label","number"}]`, and whatever repeatable field type comes after it. It passes
        # through whole. The branch below stringifies each item, which for a dict produces
        # `"{'label': 'home', 'number': '07700900123'}"` — a value that stores without complaint,
        # renders as itself on the record, and is unreadable by everything downstream. A transform
        # pipeline is `str -> str` and has nothing to say about an object, so asking for one here
        # is an authoring mistake rather than something to apply element-wise.
        if isinstance(raw, list | tuple) and raw and all(isinstance(i, dict) for i in raw):
            if self.transforms:
                raise MappingError(
                    f"transforms cannot apply to the structured answer {self.source!r}"
                )
            return list(raw)
        if isinstance(raw, list | tuple) and (
            not self.transforms or all(t in _TRIM_LIKE for t in self.transforms)
        ):
            out: list[str] = []
            for item in raw:
                if item is None:
                    continue
                value = str(item)
                for name in self.transforms:
                    fn = TRANSFORMS.get(name)
                    if fn is None:
                        raise MappingError(f"unknown transform {name!r}")
                    value = fn(value)
                value = value.strip()
                if value:
                    out.append(value)
            return out or None
        value = str(raw)
        for name in self.transforms:
            fn = TRANSFORMS.get(name)
            if fn is None:
                raise MappingError(f"unknown transform {name!r}")
            value = fn(value)
        # A value that is empty (or whitespace-only) counts as absent for required-validation, even
        # when no `trim` transform ran — but the returned value keeps its transformed form.
        return value if value.strip() else None


def parse_mapping(raw: object) -> list[FieldMapping]:
    """Validate + parse a step's authored ``mapping`` config into ``FieldMapping``s."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise MappingError("mapping must be a list")
    out: list[FieldMapping] = []
    for i, m in enumerate(raw):
        if not isinstance(m, dict):
            raise MappingError(f"mapping[{i}] must be an object")
        source, target = m.get("source"), m.get("target")
        if not isinstance(source, str) or not source:
            raise MappingError(f"mapping[{i}] needs a source")
        if not isinstance(target, str) or not target:
            raise MappingError(f"mapping[{i}] needs a target")
        transforms = m.get("transforms", [])
        if not isinstance(transforms, list) or not all(isinstance(t, str) for t in transforms):
            raise MappingError(f"mapping[{i}] transforms must be a list of names")
        unknown = [t for t in transforms if t not in TRANSFORMS]
        if unknown:
            raise MappingError(f"mapping[{i}] unknown transforms: {', '.join(unknown)}")
        out.append(
            FieldMapping(
                source=source,
                target=target,
                transforms=tuple(transforms),
                required=bool(m.get("required", False)),
            )
        )
    return out


@dataclass(frozen=True)
class MappingResult:
    patch: dict[str, object] = field(default_factory=dict)  # crm column -> value
    errors: list[str] = field(default_factory=list)


def _answer_for(answers: dict[str, object], source: str) -> object:
    """Resolve a source key: exact match first, then dotted nest into nested objects.

    A composite ``address`` field stores ``{"address": {"line1": "…"}}`` while mapping sources stay
    ``address.line1`` — prefer ``answers.get(source)`` so a flat dotted key still wins when present.
    """
    if source in answers:
        return answers[source]
    node: object = answers
    for part in source.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def apply_mapping(mappings: list[FieldMapping], answers: dict[str, object]) -> MappingResult:
    """Run the mapping against a form response → the ``crm.contacts`` patch + validation errors.
    A required source that resolves to empty is an error and is omitted from the patch."""
    patch: dict[str, object] = {}
    errors: list[str] = []
    for m in mappings:
        value = m.run(_answer_for(answers, m.source))
        if value is None:
            if m.required:
                errors.append(f"“{m.source}” is required.")
            continue
        patch[m.target] = value
    return MappingResult(patch=patch, errors=errors)
