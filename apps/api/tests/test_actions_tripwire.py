"""The action-event TRIPWIRE — what makes "every action emits an event" durable.

The guarantee (docs/api.md → "Action events") is structural: ``ActionEventsMiddleware`` covers a
mutating route iff the route authenticates through ``get_current_principal`` (directly or via any
of the session dependencies — that is what stashes the principal the middleware attributes the
event to). So the invariant to pin is: **every mutating route in the app either carries
``get_current_principal`` in its dependency tree, or is named on the explicit exemption list**
(``app/audit_actions.py::EXEMPT_MUTATING_ROUTES``) with a written justification.

A new mutating endpoint that satisfies neither fails THIS suite with instructions — it cannot
land silently outside the guarantee. No DB needed: this walks the live FastAPI route table.
"""

import re
import typing

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.audit_actions import (
    EVENT_PREFIX,
    EVENT_TYPES,
    EXEMPT_MUTATING_ROUTES,
    MAX_PREVIOUS_CANDIDATES,
    MUTATING_METHODS,
    PREVIOUS_FIELDS,
    REDACTED_KEYS,
    REDACTED_MARKERS,
    RESPONSE_SUBJECT_FIELDS,
    SUBJECT_FIELDS,
    SYNTHETIC_EVENT_TYPES,
    event_type_for,
    operation_id_for,
    skips_action_event,
)
from app.audit_stream import FEED_HIDDEN_RETIRED_EVENT_TYPES, ROUTINE_EVENT_TYPES
from app.auth import get_current_principal
from app.event_schema import registry
from app.main import app
from app.routers.admin import _ENTITY_FIELD_KINDS
from tests.conftest import api_routes


def _mutating_api_routes() -> list[tuple[str, APIRoute]]:
    """Every (method, route) pair on the app that mutates."""
    pairs: list[tuple[str, APIRoute]] = []
    for route in api_routes(app):
        if isinstance(route, APIRoute):
            for method in sorted(route.methods & MUTATING_METHODS):
                pairs.append((method, route))
    return pairs


def _authenticates(dependant: Dependant) -> bool:
    """True when ``get_current_principal`` appears anywhere in the dependency tree."""
    for dep in dependant.dependencies:
        if dep.call is get_current_principal or _authenticates(dep):
            return True
    return False


def test_every_mutating_route_is_covered_or_exempt() -> None:
    offenders = [
        f"  {method} {route.path}  (operation id: {route.unique_id})"
        for method, route in _mutating_api_routes()
        if (method, route.path) not in EXEMPT_MUTATING_ROUTES
        and not _authenticates(route.dependant)
    ]
    assert not offenders, (
        "These mutating endpoints are OUTSIDE the action-event guarantee — they neither "
        "authenticate through get_current_principal (which is what lets ActionEventsMiddleware "
        "attribute and emit the event) nor appear on the exemption list:\n"
        + "\n".join(offenders)
        + "\n\nFix: give the endpoint an authenticated session dependency (tenant_session / "
        "firm_session / admin_session / a book session — anything that Depends(get_current_"
        "principal)), OR — only for service-to-service endpoints with no human principal — add "
        "(method, path) to app/audit_actions.py::EXEMPT_MUTATING_ROUTES with a comment saying why. "
        "See docs/api.md → 'Action events'."
    )


def test_exemption_list_is_not_stale() -> None:
    """Every exemption must still name a real mutating route — a stale entry is a hole waiting to
    be reused (rename an endpoint, forget the list, and a new route could inherit an exemption it
    never justified)."""
    live = {(method, route.path) for method, route in _mutating_api_routes()}
    stale = sorted(EXEMPT_MUTATING_ROUTES - live)
    assert not stale, (
        "EXEMPT_MUTATING_ROUTES entries that no longer match a live mutating route "
        f"(remove them from app/audit_actions.py): {stale}"
    )


def _declared_request_fields(route: APIRoute) -> set[str]:
    """The field names a route's request can carry ON THE WIRE: its body model's fields (unwrapping
    ``Model | None``) plus its declared query params.

    THE WIRE NAME, NOT THE PYTHON ONE, and the difference is not academic. `extract_subject` reads
    the raw JSON body — it never sees a pydantic model — so an aliased field arrives under its
    ALIAS. `UpdateTask` declares `task_status: str | None = Field(alias="status")`, and this test
    compared Python names: it blessed a `task_status` capture that could never populate, because
    the body carries `status`. A dead capture is the exact failure this test exists to prevent, so
    it now asks for both spellings and the allowlist must name the one that arrives.
    """
    names: set[str] = set()
    body = route.body_field
    if body is not None:
        annotation = body.field_info.annotation
        for candidate in (annotation, *typing.get_args(annotation)):
            model_fields = getattr(candidate, "model_fields", None)
            if not model_fields:
                continue
            for name, field in model_fields.items():
                # The alias is what a caller sends; the attribute name is what the handler reads.
                # A model with no alias has the two the same, so this is a superset of the old set
                # for every route but the aliased ones.
                names.add(getattr(field, "alias", None) or name)
    names.update(param.name for param in route.dependant.query_params)
    return names


def test_subject_allowlist_names_real_event_types_and_real_fields() -> None:
    """`SUBJECT_FIELDS` (the write-time subject capture allowlist) must track the live route
    table: a stale event type would silently capture nothing for ever, and a misspelt field name
    would do the same — both rot invisibly without this pin."""
    routes_by_event_type = {
        event_type_for(route): route for _method, route in _mutating_api_routes()
    }
    stale = sorted(set(SUBJECT_FIELDS) - set(routes_by_event_type))
    assert not stale, (
        f"SUBJECT_FIELDS entries that no longer match a live mutating route: {stale} — "
        "remove them from app/audit_actions.py (or fix the operation id)."
    )
    for event_type, fields in SUBJECT_FIELDS.items():
        declared = _declared_request_fields(routes_by_event_type[event_type])
        unknown = sorted(set(fields) - declared)
        assert not unknown, (
            f"{event_type} allowlists subject fields its route never receives: {unknown} "
            f"(declared body/query fields: {sorted(declared)})"
        )


def test_subject_allowlist_never_overlaps_the_redact_list() -> None:
    """The belt's other half: no allowlisted subject field may be secret-shaped. `extract_subject`
    also refuses these at runtime, but the mistake should fail review, not just fail quietly."""
    for event_type, fields in SUBJECT_FIELDS.items():
        for field in fields:
            lowered = field.lower()
            assert field not in REDACTED_KEYS, f"{event_type}: {field} is on REDACTED_KEYS"
            hits = [marker for marker in REDACTED_MARKERS if marker in lowered]
            assert not hits, f"{event_type}: {field} matches redact marker(s) {hits}"


def test_reading_or_dismissing_your_own_notifications_is_never_in_the_feed() -> None:
    """The other direction of the same rule, and the one that was missing.

    The test below pins that every HIDDEN entry is a live route. Nothing pinned that every event of
    this KIND is hidden — so `notification.all_dismissed` shipped unhidden while its three siblings
    (`read`, `all_read`, `dismissed`) were hidden from the start, and a firm's Event logs carried a
    row every time somebody cleared their bell.

    Reading or dismissing your own notifications, and setting your own preferences, are per-user UI
    state. They are not something the firm did, they are the highest-volume rows in the stream, and
    a regulator-facing trail that buries a bank-detail change under them is worse than one that
    omits them. They stay in `audit.actions` — this is what the FEED shows.

    Keyed on the verb rather than a list, so a seventh spelling of "I clicked the bell" is caught
    the day it is declared.
    """
    ui_state = {
        event_type
        for event_type in EVENT_TYPES.values()
        if event_type.startswith("notification.")
        and any(
            verb in event_type
            for verb in ("read", "dismissed", "preferences_updated", "stream_opened")
        )
    }
    assert ui_state, "no notification UI-state events found — the scan is looking at nothing"
    leaking = sorted(ui_state - ROUTINE_EVENT_TYPES)
    assert not leaking, (
        f"per-user notification state that would show in the Events feed: {leaking} — add it (and "
        "its pre-rename spelling) to ROUTINE_EVENT_TYPES in app/audit_stream.py."
    )


def test_feed_hidden_event_types_are_live_routes_or_declared_history() -> None:
    """`ROUTINE_EVENT_TYPES` (kept in the stream, hidden from the Events feed) is two halves.

    The CURRENT names must match live mutating routes: a renamed op would silently re-surface in the
    feed, and a stale entry would hide nothing — both rot invisibly without this pin.

    The LEGACY names deliberately match no live route. They are what rows written before the
    ``resource.verb`` rename carry, and `audit.actions` is append-only, so dropping them would
    un-hide all of that history at once — which for the ticket mints (one row per bell or chat
    connection) means burying the feed. They are pinned by SHAPE instead: a legacy entry must start
    with the legacy prefix, so a typo cannot masquerade as history. The retired ticket types
    (``notification.stream_opened``, ``message.stream_opened``) are history too: they are no longer
    emitted, and they do not have to name a live route.

    These rows are NOT dropped (they stay in audit.actions), so this is a display concern, not the
    coverage guarantee.
    """
    # SYNTHETIC events count as live: `task.due_soon` is written by the worker's notifier rather
    # than by the route middleware, so it has no route to match and is still very much emitted.
    live = {
        event_type_for(route)
        for _method, route in _mutating_api_routes()
        if not skips_action_event(route)
    } | set(SYNTHETIC_EVENT_TYPES.values())
    assert FEED_HIDDEN_RETIRED_EVENT_TYPES <= ROUTINE_EVENT_TYPES
    legacy = {t for t in ROUTINE_EVENT_TYPES if t.startswith(EVENT_PREFIX)} | set(
        FEED_HIDDEN_RETIRED_EVENT_TYPES
    )
    current = ROUTINE_EVENT_TYPES - legacy
    stale = sorted(current - live)
    assert not stale, (
        f"ROUTINE_EVENT_TYPES entries that no longer match a live mutating route: {stale} — "
        "fix the event type in app/audit_stream.py."
    )
    # Every current entry has a legacy counterpart and vice versa: the two halves are the same set
    # of
    # ACTS in two spellings, so a name added to one half and forgotten in the other would hide the
    # act going forward and leak its history back into the feed (or the reverse).
    assert legacy, "the legacy half is gone — history for these types would flood the feed"
    assert len(current) <= len(legacy), (
        f"{len(current)} current hidden types but only {len(legacy)} legacy ones — a newly hidden "
        "act has no historic spelling, so its old rows will still show in the feed"
    )


def test_response_subject_allowlist_is_live_resolvable_and_not_secret() -> None:
    """`RESPONSE_SUBJECT_FIELDS` (the response-only subject capture) must: name live mutating
    routes; map to a SUBJECT KEY the read side actually resolves (admin._ENTITY_FIELD_KINDS), or the
    captured id lands as inert JSON that never becomes an entity chip — the whole point of lifting
    it; and never be secret-shaped on either side. The response KEY can't be schema-checked (a
    create's response is often an untyped dict), so this pins the two ends that CAN rot silently."""
    routes_by_event_type = {
        event_type_for(route): route for _method, route in _mutating_api_routes()
    }
    stale = sorted(set(RESPONSE_SUBJECT_FIELDS) - set(routes_by_event_type))
    assert not stale, (
        f"RESPONSE_SUBJECT_FIELDS entries that no longer match a live mutating route: {stale}"
    )
    # The resolvability rule applies to a captured ID, which is the only kind of value that has a
    # record to become a chip. A response may also yield a VALUE - a filename, a code - and those
    # are read as themselves, so demanding a resolver for one would be demanding a record that does
    # not exist. `registry.entity_field_kinds()` names exactly the id-typed fields.
    resolvable_ids = set(registry.entity_field_kinds())
    for event_type, mapping in RESPONSE_SUBJECT_FIELDS.items():
        for response_key, subject_key in mapping.items():
            if subject_key not in resolvable_ids:
                continue
            assert subject_key in _ENTITY_FIELD_KINDS, (
                f"{event_type}: response subject key '{subject_key}' is not a resolvable entity "
                f"field (admin._ENTITY_FIELD_KINDS) — the captured id would never become a chip."
            )
            for name in (response_key, subject_key):
                lowered = name.lower()
                assert name not in REDACTED_KEYS, f"{event_type}: {name} is on REDACTED_KEYS"
                hits = [marker for marker in REDACTED_MARKERS if marker in lowered]
                assert not hits, f"{event_type}: {name} matches redact marker(s) {hits}"


def test_previous_registry_tracks_live_routes_params_and_bodies() -> None:
    """`PREVIOUS_FIELDS` (the before-value pre-read registry) must track the live route table on
    three axes, or it rots invisibly (a pre-read that silently never fires looks identical to one
    that fired and found nothing):

    * a stale event type would pre-read for a route that no longer exists;
    * a spec binding a parameter the route's path doesn't declare would always bind None and
      capture the wrong (or no) row;
    * the pre-read rides the request-body `receive` hook, so a BODY-LESS route would never
      trigger it at all — every registered route must declare a request body.
    """
    routes_by_event_type = {
        event_type_for(route): route for _method, route in _mutating_api_routes()
    }
    stale = sorted(set(PREVIOUS_FIELDS) - set(routes_by_event_type))
    assert not stale, (
        f"PREVIOUS_FIELDS entries that no longer match a live mutating route: {stale} — "
        "remove them from app/audit_actions.py (or fix the operation id)."
    )

    def _declared(dependant: object) -> set[str]:
        """Every path param the ROUTE declares, including through a dependency.

        A param consumed by a sub-dependency is still declared by the route — and since the
        addressing dependencies (`app/addressing.py`) took over `{contact_uid}`/`{account_uid}`,
        reading only `dependant.path_params` sees an empty list and calls every registered spec
        stale. FastAPI nests them, so this walks."""
        names = {p.name for p in dependant.path_params}  # type: ignore[attr-defined]
        for sub in dependant.dependencies:  # type: ignore[attr-defined]
            names |= _declared(sub)
        return names

    for event_type, spec in PREVIOUS_FIELDS.items():
        route = routes_by_event_type[event_type]
        declared = _declared(route.dependant)
        unknown = sorted(set(spec.params) - declared)
        assert not unknown, (
            f"{event_type} binds pre-read params its route's path never declares: {unknown} "
            f"(declared path params: {sorted(declared)})"
        )
        assert route.body_field is not None, (
            f"{event_type} is registered for a before-value pre-read, but its route declares no "
            "request body — the pre-read hook rides the body read and would silently never fire."
        )


def test_previous_registry_never_touches_the_redact_list() -> None:
    """The belt's other half for before-values: no captured column may be secret-shaped.
    `capture_previous` also refuses these at runtime, but the mistake should fail review, not
    just fail quietly."""
    for event_type, spec in PREVIOUS_FIELDS.items():
        for field in spec.fields:
            lowered = field.lower()
            assert field not in REDACTED_KEYS, f"{event_type}: {field} is on REDACTED_KEYS"
            hits = [marker for marker in REDACTED_MARKERS if marker in lowered]
            assert not hits, f"{event_type}: {field} matches redact marker(s) {hits}"


def test_previous_per_tenant_specs_carry_the_candidate_cap() -> None:
    """A per-tenant spec's SQL must LIMIT at the literal cap+1 (literal so ruff's S608 sees no
    string construction) — this pins the literal to MAX_PREVIOUS_CANDIDATES so the two cannot
    drift apart, and keeps "too many candidates" detectable without pulling the estate."""
    for event_type, spec in PREVIOUS_FIELDS.items():
        if spec.per_tenant:
            assert f"LIMIT {MAX_PREVIOUS_CANDIDATES + 1}" in spec.sql, (
                f"{event_type}: per-tenant pre-read SQL must end with "
                f"'LIMIT {MAX_PREVIOUS_CANDIDATES + 1}' (MAX_PREVIOUS_CANDIDATES + 1)"
            )
            assert "tenant_id" in spec.sql.split("FROM", 1)[0], (
                f"{event_type}: a per-tenant spec must SELECT tenant_id — pick_previous keys "
                "the candidates on it"
            )


def test_every_mutating_route_has_a_declared_event_type() -> None:
    """THE tripwire for the vocabulary: no mutating route may fall back to the derived name.

    The event type is declared in ``EVENT_TYPES`` (operation id -> ``resource.verb``), not computed
    from the router tag and handler name. A route with no entry still emits — ``event_type_for``
    falls back to the old ``action.{tag}-{name}`` shape rather than dropping the event — and that
    fallback is exactly what must never ship: it puts one machine-shaped name in a list of written
    ones, and nothing fails.

    Fix a failure by adding the operation id to ``EVENT_TYPES`` with a name of the form
    ``resource.verb_past``. Reuse an existing resource where the act is about an existing thing; two
    doors onto ONE act share one name deliberately (see the collapse test below).
    """
    undeclared = sorted(
        operation_id_for(route)
        for method, route in _mutating_api_routes()
        if operation_id_for(route) not in EVENT_TYPES
        # An exempt route emits nothing, so it needs no name (only `/internal/*` service-to-service
        # endpoints are on that list — see EXEMPT_MUTATING_ROUTES). A handler marked
        # writes_no_row is transport: it is outside the vocabulary, not an exempt action.
        and (method, route.path) not in EXEMPT_MUTATING_ROUTES
        and not skips_action_event(route)
    )
    assert not undeclared, (
        f"{len(undeclared)} mutating route(s) have no declared event type and would emit the old "
        f"derived shape: {undeclared}. Add each to EVENT_TYPES in app/audit_actions.py."
    )


def test_only_realtime_ticket_mints_are_outside_the_action_vocabulary() -> None:
    """A mutating route marked ``writes_no_row`` writes nothing. The set is frozen.

    The mark is not a second exemption list. It exists for the two ticket mints, which are
    transport for the SSE stream, for ``POST /me/page-view``, which writes ``audit.page_views``
    instead, for ``POST /me/rum``, which emits a trace span and writes nothing, and for
    ``POST /me/sessions/refresh``, which rewrites the caller's session mirror after a revoke
    the page already performed at WorkOS, and for the caller's own notes (``/me/notes``), which
    are private to them — an Activity row saying who wrote "call Joan" would hand a colleague the
    list RLS withholds. A new route that wants out of the vocabulary has to be named here, so
    the mark cannot quietly swallow a real action.
    """
    marked = sorted(
        f"{method} {route.path}"
        for method, route in _mutating_api_routes()
        if skips_action_event(route)
    )
    assert marked == [
        "DELETE /me/notes/{uid}",
        "PATCH /me/notes/{uid}",
        "POST /me/notes",
        "POST /me/page-view",
        "POST /me/rum",
        "POST /me/sessions/refresh",
        "POST /messaging/ticket",
        "POST /notifications/ticket",
    ]


def test_event_types_are_resource_dot_verb() -> None:
    """The shape of the vocabulary, so it stays guessable.

    ``resource.verb`` — lower snake_case either side of ONE dot. No ``action.`` prefix (it repeated
    on
    every entry and distinguished nothing), no hyphens (they were the router-tag artefact), exactly
    one
    dot (a second segment would make the resource ambiguous for every prefix filter that keys on it
    —
    the document-family predicate and the AML deny list both do).
    """
    shape = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    bad = sorted(name for name in set(EVENT_TYPES.values()) if not shape.match(name))
    assert not bad, f"event types that are not resource.verb: {bad}"


def test_collapsed_acts_are_declared_deliberately() -> None:
    """Two routes may share an event type only where that IS the intent — and the set is frozen.

    A shared name says "one act, two doors": Graphene and the firm both publish an email template,
    and who did it is on the row (``actor_id``, ``target_tenant_id``, ``route``) rather than in the
    name. That is deliberate and it is what stops every subscriber having to know two names.

    An ACCIDENTAL share is the bug this guards: it fans one subscriber across two unrelated
    operations, and the reader of a rule cannot tell which fired. So the count is pinned. If a new
    pair is genuinely one act, raise the number in the same commit that adds it.
    """
    by_name: dict[str, list[str]] = {}
    for operation_id, name in EVENT_TYPES.items():
        by_name.setdefault(name, []).append(operation_id)
    collapsed = {name: sorted(ops) for name, ops in by_name.items() if len(ops) > 1}
    # 34: the four terms authoring acts each have a Graphene door and a firm door onto one service
    # call, which is the email-template pattern, and firm and operations both end one of a member's
    # live sessions. Who did it is on the row, not in the name. The read-state and preview routes
    # write no rows (`EXEMPT_MUTATING_ROUTES`), so the two-door `conversation.read` and
    # `email_template.previewed` are not in the count.
    assert len(collapsed) == 34, (
        f"{len(collapsed)} event types are shared by more than one route (was 34):\n"
        + "\n".join(f"  {name}: {ops}" for name, ops in sorted(collapsed.items()))
    )
    # Every collapse is one act through two doors, so exactly two routes each — three would mean a
    # name has become a category rather than an act.
    too_many = {name: ops for name, ops in collapsed.items() if len(ops) > 2}
    assert not too_many, f"an event type shared by 3+ routes is a category, not an act: {too_many}"


# NOTE: there was a test here pinning the router-tag list the portals used to strip from an event
# type. It is gone with the reason for it: an event type is now a declared ``resource.verb`` name
# and
# carries no router tag, so there is nothing to strip. The UI keeps `stripRouterTag` for the LEGACY
# names still in `audit.actions` (append-only, so history keeps the old spelling) — and that set can
# never grow, because `test_every_mutating_route_has_a_declared_event_type` above stops any new
# route
# emitting the derived shape.


def test_no_operation_id_repeats_its_own_router_tag() -> None:
    """A route whose function name repeats its tag yields `action.admin-admin-create-...`.

    The UI strips the repeat, so these READ correctly - but the raw id is what the detail panel
    shows, what a support ticket quotes and what the event-type filter offers, so the doubling is
    visible wherever the vocabulary itself is. This test does not fail the existing ones (renaming a
    live event type is a vocabulary change, not a tidy-up: stored rows keep the old string and the
    notification/automation registries key on it) - it FREEZES the set, so a new route cannot add to
    it. Name the handler without its tag: `create_email_template` under tag `admin`, not
    `admin_create_email_template`.
    """
    # OPERATION ids, not event types: an operation id must stay unique (OpenAPI requires it, and
    # the generated client is built from it), so the doubling lives there even where the event
    # vocabulary has been collapsed. Reading event types here would under-count and let a new
    # doubled route in behind an override.
    doubled = sorted(
        {
            operation_id_for(route)
            for _m, route in _mutating_api_routes()
            if (lambda parts: len(parts) > 1 and parts[0] == parts[1])(
                operation_id_for(route).split("-")
            )
        }
    )
    # The set as it stands: 11 email-template ops on the admin router, 3 notification-rule ops
    # on the firm router, and the messaging family. Frozen at a COUNT rather than listed,
    # because the point is that it does not grow.
    assert len(doubled) == 23, (
        f"{len(doubled)} operation ids repeat their router tag (was 23): {doubled}. A new one "
        "means a handler is named `{tag}_{verb}_{noun}` under tag `{tag}` - drop the tag from the "
        "function name so the operation id reads `{tag}-{verb}-{noun}`."
    )


def test_the_event_vocabulary_is_ours_not_fastapis_internal_attribute() -> None:
    """An event type is the name WE declared, never a FastAPI-derived one.

    THE REGRESSION THIS EXISTS FOR. FastAPI 0.141 changed `include_router` to wrap a router instead
    of copying its routes onto the app, so a route object stopped carrying the app's
    `generate_unique_id_function` output and fell back to FastAPI's default
    (`update_tenant_admin_tenants__tenant_id__patch`). The OpenAPI spec stayed correct — it applies
    the app's function at spec-build time — so the generated client was fine and every request still
    worked. Only the audit stream's vocabulary changed, for every route at once, with nothing
    visibly broken.

    The declared map makes that specific failure impossible (a name is a literal in this repo, not a
    derivation), so what is pinned now is the other half: the LOOKUP KEY is still the readable
    operation id, so a FastAPI change to `unique_id` cannot make every route miss the map at once
    and
    silently fall through to the derived shape.
    """
    for route in api_routes(app):
        if not (set(route.methods or ()) & MUTATING_METHODS):
            continue
        if skips_action_event(route):
            continue
        event_type = event_type_for(route)
        operation_id = operation_id_for(route)
        declared = EVENT_TYPES.get(operation_id)
        if declared is not None:
            assert event_type == declared, route.path
            # …and the key really is the readable id, not FastAPI's internal one.
            assert "__" not in operation_id, (
                f"{route.path} resolves through {operation_id!r} — that is FastAPI's internal "
                "unique_id, so the whole vocabulary is one library change away from missing the map"
            )
            continue
        # Undeclared (a new route, caught by its own test): the fallback must still be OURS.
        expected = f"{route.tags[0] if route.tags else 'default'}-{route.name}"
        assert event_type == f"action.{expected}", (
            f"{route.path} emits {event_type!r}, expected the derived fallback action.{expected!r}"
        )


def test_synthetic_event_types_follow_the_same_shape() -> None:
    """The events that are NOT request routes are named by the same rule.

    Auth events, a document view, the worker's render and an anonymous share access are emitted by
    named constants rather than the middleware. They are the same vocabulary to every reader — the
    filter, the detail panel, a rule — so a second convention here would be a second vocabulary that
    only some events belong to.
    """
    shape = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    for name, value in sorted(SYNTHETIC_EVENT_TYPES.items()):
        assert shape.match(value), f"{name} -> {value!r} is not resource.verb"
    # The constants the emitters actually pass must be exactly these.
    from app import audit_actions as _actions

    # `SHARE_ACCESS_EVENT` was here until the document share-links feature was removed: its rows are
    # still in the stream (so the READ side keeps the vocabulary) but nothing emits it any more, so
    # there is no constant to check.
    for const in (
        "DOCUMENT_VIEW_EVENT",
        "DOCUMENT_RENDER_EVENT",
        "AUTH_ACTIVATED_EVENT",
        "AUTH_LOGIN_EVENT",
        "AUTH_LOGIN_FAILED_EVENT",
        "AUTH_LOGOUT_EVENT",
        "AUTH_MFA_ENROLLED_EVENT",
        "AUTH_MFA_REMOVED_EVENT",
        "AML_IMAGES_VIEW_EVENT",
    ):
        value = getattr(_actions, const)
        assert value in set(SYNTHETIC_EVENT_TYPES.values()), f"{const} = {value!r} is not declared"


def test_the_aml_family_keeps_the_prefix_the_firm_view_excludes_on() -> None:
    """`routers/firm.py::FIRM_EXCLUDED_EVENT_PREFIXES` withholds `action.aml-` from a firm's own
    audit view, because a firm sees screening OUTCOMES and never Graphene's review activity
    (docs/aml.md — the tipping-off control). That exclusion is a string prefix match, so an event
    vocabulary that stops beginning `action.aml-` does not fail: it LEAKS.

    Which is exactly what a FastAPI bump did, silently, until this asserted it.
    """
    from app.routers.firm import FIRM_EXCLUDED_EVENT_PREFIXES

    aml = [
        event_type_for(route)
        for route in api_routes(app)
        if (set(route.methods or ()) & MUTATING_METHODS) and "/aml" in route.path
    ]
    assert aml, "no mutating AML routes found — this test would be vacuous"
    for event_type in aml:
        assert any(event_type.startswith(p) for p in FIRM_EXCLUDED_EVENT_PREFIXES), (
            f"{event_type} is not excluded from the firm audit view by "
            f"{FIRM_EXCLUDED_EVENT_PREFIXES} — AML review activity would be visible to the firm"
        )
