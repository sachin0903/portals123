"""Typed access to the vendor's documented field contracts (`vendor_manifest.json`).

The JSON is a machine transcription of https://dev.api.morgan-lloyd.co.uk/, produced by
`scripts/build_manifest.py` and re-checkable with `--check`. This module is the read side: it
answers "what fields does `cam_createcontact` take, which are required, and what values does
`cam_title` accept" for the two consumers that must not guess -- the admin action forms, which
build real payloads, and the entity tables, which choose columns.

TWO THINGS THIS MODULE ADDS THAT THE TRANSCRIPTION DELIBERATELY DOES NOT.

**The name reconciliation.** `docs/morgan-lloyd/endpoint-coverage.md` §1 records eleven
operations whose documented name is not the action the client actually POSTs, and the CLIENT is
authoritative there -- those names are verified against the live org. The transcription stays
pure (it says what the page says), and the mapping lives here, where it can be read next to the
reason. A twelfth was found while building this: the doc's **"Create Investment" is
`cam_createwrapper`** -- there is no `cam_createinvestment` in the org, which `$metadata` had
already established.

**The option-set reconciliation** is NOT here: it is a test. `enums.py` was hand-transcribed
from the same page and is the typed vocabulary the client already uses; duplicating it would
give two sources that drift. `tests/test_manifest.py` asserts the two agree, so the manifest
carries the field lists (which `enums.py` has never held) and `enums.py` keeps the values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

__all__ = [
    "ACTION_ALIASES",
    "Field",
    "Operation",
    "Option",
    "load",
    "operation",
    "operations",
]


# Documented name -> the action the client actually POSTs. Dynamics action names are CASE
# SENSITIVE, so these are exact. Sourced from docs/morgan-lloyd/endpoint-coverage.md §1, which
# was written by comparing the catalogue against `pkgs/morgan-lloyd` and, where it says
# "verified live", against the org itself.
ACTION_ALIASES: dict[str, str] = {
    "cam_createbenefitapplication": "cam_setmemberdetails",
    "cam_retrievememberdetails": "cam_memberdetails",
    "cam_getschemebyschemref": "cam_getscheme",
    "cam_getalltransfersbysceme": "cam_getalltransfersbyscheme",
    "cam_gettransfer": "cam_getpensiontransfer",
    "cam_getbeneficiariesbyschemeref": "cam_schemebeneficiaries",
    "cam_getbenefitevents": "cam_schemememberevents",
    "cam_createbeneficiary": "cam_createbeneficiaries",
    "cam_createpensioncontribution": "cam_createcontribution",
    "cam_updatepensioncontribution": "cam_updatecontribution",
    # The twelfth, found by parsing the page rather than reading it: the "Create Investment"
    # section POSTs cam_createwrapper. `cam_createinvestment` does not exist in the org.
    "cam_createinvestment": "cam_createwrapper",
}


@dataclass(frozen=True, slots=True)
class Option:
    """One member of a documented option set.

    `label` is None for `cam_taxrelief`, whose four values the page lists bare and describes in a
    separate lookup table -- so a caller renders `lookups` for those rather than inventing names.
    """

    label: str | None
    value: int


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    type: str
    required: bool
    description: str
    options: tuple[Option, ...] = ()
    multiselect: bool = False
    """Several members at once, sent as a comma-separated STRING of their numbers.

    From the page's own wording ("String representing a multiselect optionset value"), not from
    the type -- the vendor types these `string`, indistinguishable from a free-text name. It
    matters because the FCA's four vulnerability drivers COMPOUND: a control offering one member
    cannot record a client who is in poor health and has just been bereaved, and there is no error
    to notice, only a record that is quietly less true than the conversation it came from.
    """
    required_note: str = ""
    """The vendor's own marking, verbatim -- 'Yes', 'No', 'No*', 'No**'.

    Kept beside the boolean because the asterisks are conditions, not decoration: `No*` means
    required when the contact has an adviser firm, `No**` required when the contact is marked
    vulnerable. A form that treated either as simply optional would build a payload the vendor
    rejects, and one that treated them as required would block a legitimate call.
    """

    @property
    def conditional(self) -> bool:
        """Required only in circumstances the page states in prose."""
        return "*" in self.required_note


@dataclass(frozen=True, slots=True)
class Operation:
    section: str
    title: str
    action: str
    method: str
    path: str
    fields: tuple[Field, ...]
    response_fields: tuple[Field, ...]
    parameters: tuple[Field, ...] = ()
    """URL parameters -- path segments, not payload. `GUID` on every entity PATCH.

    Separated from `fields` because putting it there made each PATCH look like a one-field
    endpoint whose one field was the record id."""
    lookups: tuple[dict[str, str], ...] = ()

    @property
    def required_fields(self) -> tuple[Field, ...]:
        return tuple(f for f in self.fields if f.required)

    def field(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)


def _field(raw: dict[str, Any]) -> Field:
    return Field(
        name=str(raw.get("name", "")),
        type=str(raw.get("type", "")),
        required=bool(raw.get("required_flag")),
        description=str(raw.get("description", "")),
        options=tuple(
            Option(label=o.get("label"), value=int(o["value"])) for o in raw.get("options") or []
        ),
        multiselect=bool(raw.get("multiselect")),
        required_note=str(raw.get("required", "")),
    )


@cache
def load() -> dict[str, Any]:
    """The raw transcription. Cached: it is a static file read once per process."""
    text = resources.files("morgan_lloyd").joinpath("vendor_manifest.json").read_text("utf-8")
    return dict(json.loads(text))


@cache
def operations() -> tuple[Operation, ...]:
    """Every documented operation that names an action, in page order."""
    out: list[Operation] = []
    for section in load()["sections"]:
        for raw in section["operations"]:
            action = raw.get("action")
            if not action:
                continue
            out.append(
                Operation(
                    section=section["title"],
                    title=raw["title"],
                    action=action,
                    method=raw.get("method", ""),
                    path=raw.get("path", ""),
                    fields=tuple(_field(f) for f in raw.get("fields") or []),
                    response_fields=tuple(_field(f) for f in raw.get("response_fields") or []),
                    parameters=tuple(_field(f) for f in raw.get("parameters") or []),
                    lookups=tuple(raw.get("lookups") or []),
                )
            )
    return tuple(out)


def operation(name: str, method: str | None = None) -> Operation | None:
    """Look one up by action name, or by the name the catalogue calls it.

    `method` MATTERS FOR THE ENTITY ENDPOINTS, and omitting it was a silent bug. A custom action
    name identifies exactly one operation, but an entity set does not: `contacts` is Get All
    Contacts, Get Individual Contact, Update Contact and Get Financial Adviser Contacts, and
    `cam_nominationofbeneficiaries` is three more. Returning the first match handed the caller a
    GET -- which has no request fields -- so Update Beneficiary looked like an operation with no
    documented contract, and the console papered over it by borrowing the create's twelve fields.

    Falls back to the first match when no method is given or none matches, so every existing
    caller keeps working and a name that is genuinely unique needs no method.

    Aliases are tried second, deliberately: an alias must never shadow a real action. `resolved`
    could in principle collide with a genuine action name, and the live one has to win.
    """
    matches = [op for op in operations() if op.action == name]
    if not matches:
        resolved = ACTION_ALIASES.get(name)
        matches = [op for op in operations() if op.action == resolved] if resolved else []
    if not matches:
        return None
    if method:
        wanted = method.strip().upper()
        for op in matches:
            if op.method.upper() == wanted:
                return op
    return matches[0]
