"""Every event carries what it takes to say what was done — checked over the LIVE route table.

The rule, in Craig's words: "all the details to replicate that event are included". A row that says
a transfer's status changed without saying to what is a record of an act nobody can reconstruct.

Every event already carries the method, the route template, the path params and the query. Per
event it carries `metadata.subject` (the value allowlist), `metadata.previous` (the before-value
pre-read) and `metadata.changed` (body key NAMES only). So this walks every mutating route, takes
the fields its body declares ON THE WIRE, and refuses any that is a CLOSED value — an enum, a bool,
a number, a date, an identifier — and is not captured. Those are the ones it is both safe and free
to record.

THE LINE IT DOES NOT CROSS is `_PERSONAL` and `_SECRET`. A client's date of birth, gender, tax
position or bank details are closed values and are still not recorded as subjects:
`metadata.changed` names WHICH of them moved, and the value stays in the record. Free text is
refused for the same reason.

The audit that produced this found four things, each of which the test now prevents:
  * `task.updated` captured `task_status`, the PYTHON name of a field aliased to `status`, so the
    capture had never once populated.
  * 36 events carried none of the closed values that decide their outcome.
  * a field is only capturable when EVERY door of an act declares it.
  * a multipart route can carry no subject at all — `extract_subject` parses JSON.
"""

from __future__ import annotations

import datetime as dt
import typing

import pytest
from fastapi.routing import APIRoute

from app.audit_actions import (
    BODY_CAPTURE_EVENTS,
    EXEMPT_MUTATING_ROUTES,
    SUBJECT_FIELDS,
    event_type_for,
    skips_action_event,
)
from app.event_schema.classify import recordable
from app.main import app
from tests.conftest import api_routes

#: Deliberate exceptions, each with its reason. Nothing lands here by being inconvenient.
_ALLOWED_GAPS: dict[tuple[str, str], str] = {
    # Names which integration the link screen was open on, so the API knows which provider's plans
    # are valid. It decides nothing about the act: the account is what is linked, to a plan of a
    # client. Recording it would need an `integration` entity kind through the whole read side
    # (TAG_FIELDS, the label resolvers) for a chip that adds nothing the account row does not.
    ("integration.account_linked_to_io_plan", "integration_uid"): "context, not a decision",
}


def _wire_fields(route: APIRoute) -> dict[str, object]:
    """The body fields a caller can send, by WIRE name, with their annotations.

    The wire name matters: `extract_subject` reads the raw JSON, so an aliased field arrives under
    its alias (`UpdateTask.task_status` is `status` on the wire). Query params are excluded —
    they are recorded on every event already.
    """
    out: dict[str, object] = {}
    body = route.body_field
    if body is None:
        return out
    annotation = body.field_info.annotation
    for candidate in (annotation, *typing.get_args(annotation)):
        model_fields = getattr(candidate, "model_fields", None)
        if not model_fields:
            continue
        for name, field in model_fields.items():
            out[getattr(field, "alias", None) or name] = field.annotation
    return out


def _is_multipart(route: APIRoute) -> bool:
    """A multipart body never reaches `extract_subject` — it parses JSON, and the body cap drops an
    upload. Nothing on such a route is capturable, so nothing on it can be a gap."""
    body = route.body_field
    media = getattr(getattr(body, "field_info", None), "media_type", "") or ""
    return "multipart" in media or "form-data" in media


def _events() -> dict[str, list[APIRoute]]:
    """Every mutating route, grouped by the event it emits.

    `api_routes(app)`, never `app.routes`: since FastAPI 0.141 the latter is not a flat list, so
    iterating it finds NOTHING and a test written over it passes by describing an empty set. The
    first cut of this module did exactly that and reported a clean audit over zero events.
    """
    out: dict[str, list[APIRoute]] = {}
    for route in api_routes(app):
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or ():
            if method not in {"POST", "PUT", "PATCH", "DELETE"}:
                continue
            # An EXEMPT route emits no event at all (`EXEMPT_MUTATING_ROUTES`: the token-gated
            # `/internal/*` endpoints and the one POST that performs nothing), and a
            # `writes_no_row` route writes nothing anywhere. Neither can have a gap — there is
            # no row to be missing anything from.
            if (method, route.path) not in EXEMPT_MUTATING_ROUTES and not skips_action_event(route):
                out.setdefault(event_type_for(route), []).append(route)
            break
    return out


def _gaps() -> dict[str, list[str]]:
    """event type -> the closed-vocabulary body fields it does not capture."""
    out: dict[str, list[str]] = {}
    for event_type, routes in _events().items():
        if any(_is_multipart(r) for r in routes):
            continue
        # A captured body carries every field, so nothing on such an event can be missing.
        if event_type in BODY_CAPTURE_EVENTS:
            continue
        # INTERSECTED across the act's doors: a field only one door declares cannot be allowlisted
        # (the allowlist is validated against one of them), so it is not a gap either.
        shared: dict[str, object] | None = None
        for route in routes:
            fields = _wire_fields(route)
            shared = fields if shared is None else {k: v for k, v in shared.items() if k in fields}
        captured = set(SUBJECT_FIELDS.get(event_type, ()))
        missing = sorted(
            name
            for name, annotation in (shared or {}).items()
            if name not in captured
            and name != "tenant"
            and recordable(name, annotation)
            and (event_type, name) not in _ALLOWED_GAPS
        )
        if missing:
            out[event_type] = missing
    return out


def test_every_event_records_the_closed_values_that_decide_it() -> None:
    """THE AUDIT, as a gate. A new route whose body carries a status, an amount, a date or an id
    that the event does not record fails here — which is the only moment anybody is looking."""
    gaps = _gaps()
    assert not gaps, (
        "these events do not record closed values their route receives — add them to "
        "app/event_schema/registry.py, or add the field to `_ALLOWED_GAPS` here with the reason:\n"
        + "\n".join(f"  {event}: {', '.join(fields)}" for event, fields in sorted(gaps.items()))
    )


def test_no_capture_names_a_field_the_wire_never_carries() -> None:
    """The bug this whole audit found: `task.updated` allowlisted `task_status`, the PYTHON name of
    a field aliased to `status`. `extract_subject` reads the raw JSON body, so it matched nothing a
    caller ever sent and the status of every task update went unrecorded — a capture that looks
    present and is dead. `test_actions_tripwire` now compares wire names for the same reason; this
    states the rule from the replayability side, where the consequence is felt."""
    events = _events()
    dead: list[str] = []
    for event_type, fields in SUBJECT_FIELDS.items():
        routes = events.get(event_type)
        if not routes:
            continue
        wire = set().union(*(set(_wire_fields(r)) for r in routes))
        query = {p.name for r in routes for p in r.dependant.query_params}
        response_only = {"contact_id", "user_id", "document_uids", "client_contact_id"}
        for field in fields:
            if field not in wire and field not in query and field not in response_only:
                dead.append(f"{event_type}.{field}")
    assert not dead, f"captures that can never populate (wrong name, or an alias): {sorted(dead)}"


@pytest.mark.parametrize(
    ("name", "annotation", "expected"),
    [
        ("status", typing.Literal["open", "done"], True),
        ("enabled", bool, True),
        ("amount", float, True),
        ("due_date", dt.date, True),
        ("model_id", int, True),
        ("flow_uid", str, True),  # id-shaped NAME
        ("description", str, False),  # prose
        ("title", str, False),
        ("dob", dt.date, False),  # personal, though closed
        ("gender", typing.Literal["male", "female"], False),
        ("sort_code", str, False),
        ("workos_api_key", str, False),  # secret-shaped
        ("answers", dict, False),  # an object
    ],
)
def test_what_counts_as_a_recordable_value(name: str, annotation: object, expected: bool) -> None:
    """The judgement this audit rests on, stated directly: a closed vocabulary or an identifier is
    recordable; prose, an object, personal data and anything secret-shaped are not."""
    assert recordable(name, annotation) is expected


def test_the_deliberate_gaps_are_declared_with_a_reason() -> None:
    """An exception has to be argued for, not merely listed."""
    for (event, field), reason in _ALLOWED_GAPS.items():
        assert event and field and len(reason) > 20, f"{event}.{field}: give a real reason"
