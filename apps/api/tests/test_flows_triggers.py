"""The trigger registry: pinned to the contract, and tripwired against the live route table.

Two guarantees, both structural:

* **The contract pin** — ``app/flows/triggers.py`` is a Python MIRROR of
  ``pkgs/ui/src/lib/components/book/journeys/flow-contract.json`` (the API container ships without
  pkgs/ui, so Python cannot read the JSON at runtime). These tests are what stop the two drifting;
  the technique is copied from ``tests/test_branding.py``'s block-contract pin, path resolution
  included.
* **The tripwire** — every ``source="action"`` entry must resolve to a LIVE route's event type,
  enumerated off the FastAPI app exactly as ``tests/test_actions_tripwire.py`` does. Rename an
  endpoint without re-pointing the registry and this fails loudly, instead of every firm's
  automations silently disarming — a failure with no symptom until a client is not chased.

No DB needed: the route table and two JSON files.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute

from app.audit_actions import MUTATING_METHODS, event_type_for
from app.flows import triggers
from app.main import app
from app.permissions import ALL as ALL_PERMISSIONS
from tests.conftest import api_routes

# tests/ -> apps/api -> apps -> the repo root.
_CONTRACT_PATH = (
    Path(__file__).resolve().parents[3]
    / "pkgs"
    / "ui"
    / "src"
    / "lib"
    / "components"
    / "book"
    / "journeys"
    / "flow-contract.json"
)
_DRIFT = "the Python side drifted from flow-contract.json — change them together"


def _contract() -> dict[str, Any]:
    data = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _live_event_types() -> set[str]:
    """Every event type the action stream can actually emit, from the live route table."""
    return {
        event_type_for(route)
        for route in api_routes(app)
        if isinstance(route, APIRoute) and (route.methods & MUTATING_METHODS)
    }


# ---- the contract pin -------------------------------------------------------------------------


@pytest.mark.pkgs_ui_pin
def test_trigger_registry_matches_the_contract() -> None:
    """Key for key, field for field. A trigger the builder offers and the engine has never heard
    of is an automation an author can arm that will never fire."""
    spec = _contract()["triggers"]
    assert set(triggers.TRIGGERS) == set(spec), _DRIFT
    for key, entry in spec.items():
        mine = triggers.TRIGGERS[key]
        assert mine.label == entry["label"], f"{key}: {_DRIFT}"
        assert mine.source == entry["source"], f"{key}: {_DRIFT}"
        assert mine.event == entry["event"], f"{key}: {_DRIFT}"
        assert mine.subject == entry["subject"], f"{key}: {_DRIFT}"


def test_trigger_sources_and_subjects_are_from_the_declared_vocabularies() -> None:
    for key, spec in triggers.TRIGGERS.items():
        assert spec.source in triggers.SOURCES, key
        assert spec.subject in triggers.SUBJECT_KINDS, key
        # A schedule trigger has no event; everything else must name one.
        assert (spec.event is None) == (spec.source == "schedule"), key


def _without_comments(block: dict[str, Any]) -> dict[str, Any]:
    """The contract carries its rationale inline as ``$comment_*`` keys (they are the reason it
    reads as a document rather than a config blob). Only the VALUES are the pin."""
    return {k: v for k, v in block.items() if not k.startswith("$comment")}


@pytest.mark.pkgs_ui_pin
def test_safety_defaults_match_the_contract() -> None:
    """These are the numbers the guards actually use. Drift here is a guard running at a limit
    nobody agreed to."""
    assert triggers.SAFETY == _without_comments(_contract()["safety"]), _DRIFT


@pytest.mark.pkgs_ui_pin
def test_caps_match_the_contract() -> None:
    assert triggers.CAPS == _without_comments(_contract()["caps"]), _DRIFT


@pytest.mark.pkgs_ui_pin
def test_permissions_match_the_contract_and_are_registered() -> None:
    """The contract names the slugs; ``app/permissions.py`` is where they actually exist. Both
    must agree, including the PORTAL — ``automations:global_pause`` being an operations slug is
    the whole mechanism that keeps it off every firm-scoped role (rbac.OPERATIONS_ONLY)."""
    assert triggers.PERMISSIONS == _without_comments(_contract()["permissions"]), _DRIFT
    registered = {p.slug: p.portal for p in ALL_PERMISSIONS}
    for slug, portal in triggers.PERMISSIONS.items():
        assert slug in registered, f"{slug} is in the contract but not in app/permissions.py"
        assert registered[slug] == portal, (
            f"{slug}: declared against the {registered[slug]!r} portal, contract says {portal!r}"
        )


def test_global_pause_is_operations_reserved() -> None:
    """Structural, not a check somewhere that has to remember to fire: because the slug is
    declared against the operations portal it lands in ``rbac.OPERATIONS_ONLY``, and
    ``assert_grantable`` refuses it to every firm-scoped role."""
    from app import rbac

    assert "automations:global_pause" in rbac.OPERATIONS_ONLY
    # ...and the firm-facing three are NOT reserved — a firm must be able to pause itself.
    for slug in ("automations:read", "automations:write", "automations:pause"):
        assert slug not in rbac.OPERATIONS_ONLY, slug


# ---- the tripwire -----------------------------------------------------------------------------


def test_every_action_trigger_resolves_to_a_live_route() -> None:
    """THE tripwire. An action-sourced trigger whose event type no longer matches a route is an
    automation that will never fire again, with no error anywhere — so it fails here instead."""
    live = _live_event_types()
    orphans = sorted(
        f"  {key} -> {spec.event}"
        for key, spec in triggers.TRIGGERS.items()
        if spec.source == "action" and spec.event not in live
    )
    assert not orphans, (
        "These triggers name an event type no live route emits — every firm's automations armed "
        "on them are silently disarmed:\n"
        + "\n".join(orphans)
        + "\n\nFix: re-point the `event` in app/flows/triggers.py AND in "
        "pkgs/ui/src/lib/components/book/journeys/flow-contract.json (change them together) to "
        "the renamed route's operation id — 'action.' + '{tag}-{route name}'."
    )


def test_pending_domain_events_are_honest() -> None:
    """A domain-sourced trigger the engine does not emit yet must be listed as pending, and a
    listed one must still be pending. The pair is what turns "we'll wire it in Phase B" into
    something a test will remind us about rather than something we discover in production."""
    domain = {s.event for s in triggers.TRIGGERS.values() if s.source == "domain" and s.event}
    unlisted = sorted(domain - set(triggers.PENDING_DOMAIN_EVENTS))
    assert not unlisted, (
        f"domain triggers with no producer and no entry in PENDING_DOMAIN_EVENTS: {unlisted}"
    )
    stale = sorted(set(triggers.PENDING_DOMAIN_EVENTS) - domain)
    assert not stale, f"PENDING_DOMAIN_EVENTS names events no trigger uses: {stale}"
    # The consumer must not pretend it can serve them.
    assert "domain" not in triggers.SUPPORTED_SOURCES
    assert triggers.SUPPORTED_SOURCES <= triggers.SOURCES


def test_event_reverse_map_covers_every_evented_trigger() -> None:
    for key, spec in triggers.TRIGGERS.items():
        if spec.event:
            assert key in triggers.keys_for_event(spec.event)
    assert triggers.keys_for_event("action.not-a-real-operation") == ()


def test_resolve_returns_none_for_an_unknown_key() -> None:
    """A stored row naming a trigger this deployment does not have (a rollback, a hand-edit) must
    be a recordable skip, not an exception that wedges the loop for every other tenant."""
    assert triggers.resolve("nope.not.here") is None
    assert triggers.resolve("contact.created") is not None


def test_the_flow_dispatch_target_is_registered() -> None:
    """Importing the consumer registers the ``flow`` target; the ``action`` kind is the rule
    tier's seam and is deliberately absent until its executor is imported."""
    from app.flows import automation  # noqa: F401 — the import IS the registration

    assert triggers.target_for("flow") is not None
    assert triggers.target_for("nonsense") is None


def test_ceilings_are_at_or_above_the_defaults() -> None:
    """A ceiling BELOW its default would silently lower the guard for every tenant that never
    touched a setting — the opposite of what a ceiling is for."""
    for key, ceiling in triggers.CEILINGS.items():
        assert ceiling >= triggers.SAFETY[key], key


# ---- listener deliverability (the wait_for_event half) ------------------------------------------


@pytest.mark.pkgs_ui_pin
def test_listener_sets_match_the_contract() -> None:
    """``listeners`` is the contract block that says which triggers a PARKED case may wait for —
    a strictly narrower set than the rule tier's menu, and the answer publish, ``arm_listener`` and
    the builder all read. Drift here is how ``schedule.recurring`` came to be offered as something
    to wait for."""
    listeners = _contract()["listeners"]
    assert triggers.LISTENER_SOURCES == frozenset(listeners["deliverableSources"]), _DRIFT
    assert triggers.LISTENER_SUBJECTS == frozenset(listeners["scopableSubjects"]), _DRIFT
    # Both sets must be subsets of the vocabularies they draw from, or a typo reads as "nothing
    # qualifies" and every wait_for_event node is silently refused.
    assert triggers.LISTENER_SOURCES <= triggers.SOURCES
    assert triggers.LISTENER_SOURCES <= triggers.SUPPORTED_SOURCES
    assert triggers.LISTENER_SUBJECTS <= triggers.SUBJECT_KINDS


def test_a_schedule_can_never_be_waited_for() -> None:
    """The bug this check exists for. A schedule trigger has ``event = None``, so no event type can
    ever resolve to it and ``schedule_once`` never touches listeners — a case parked waiting for
    one is wedged permanently, and the builder used to offer it ENABLED because it asked the RULE
    tier's question (``supported``) instead of this one."""
    spec = triggers.TRIGGERS["schedule.recurring"]
    assert triggers.supported(spec) is True, "still perfectly good as a rule trigger"
    assert triggers.listenable("schedule.recurring") is False
    assert "schedule" in (triggers.listener_refusal(spec) or "")


def test_a_domain_trigger_can_never_be_waited_for_yet() -> None:
    """Disabled in the builder but published clean through the API, which is the boundary. Nothing
    consumes ``flows.domain_events`` looking for listeners, so these are undeliverable today; they
    become deliverable the day the consumer lands, via SUPPORTED_SOURCES and this same set."""
    for key in ("case.completed", "case.step_completed", "screening.completed", "tag.applied"):
        assert triggers.listenable(key) is False, key


def test_a_task_trigger_can_never_be_waited_for() -> None:
    """The judgement call, pinned. ``task.completed`` LOOKS deliverable — action-sourced, live
    route, ``supported`` is True — but a case's subject is a contact or an account, never a task
    (``timers._case_subject`` has nothing honest to return), so ``arm_listener`` silently armed
    nothing and "wait until the task is done" became "don't wait". Refused, not degraded."""
    assert "task" not in triggers.LISTENER_SUBJECTS
    for key in ("task.created", "task.completed"):
        assert triggers.supported(triggers.TRIGGERS[key]) is True
        assert triggers.listenable(key) is False, key
        assert "task" in (triggers.listener_refusal(triggers.TRIGGERS[key]) or "")


def test_the_ordinary_triggers_are_still_waitable() -> None:
    """The check must not have swept up the ones that work — the contract's own example ("no
    signed form in 7 days → chase") has to stay authorable."""
    for key in ("client.form_submitted", "client.document_uploaded", "account.model_changed"):
        assert triggers.listenable(key) is True, key
        assert triggers.listener_refusal(triggers.TRIGGERS[key]) is None


def test_an_unknown_key_is_not_listenable_but_has_its_own_refusal() -> None:
    assert triggers.listenable("nope.not.here") is False


def test_every_listenable_trigger_can_have_its_subject_resolved_at_release() -> None:
    """The other half of deliverability, and the reason ``task.created`` would fail even if a case
    COULD be about a task: releasing a listener needs the consumer to name the event's subject, and
    it can only do that for events in ``automation._SUBJECTS``. A trigger we declare waitable but
    the consumer cannot scope is a case that waits for ever — the exact failure this set exists to
    prevent, arriving from the other side."""
    from app.flows import automation

    for key, spec in triggers.TRIGGERS.items():
        if not triggers.listenable(key):
            continue
        assert spec.event in automation._SUBJECTS, (
            f"{key} is declared waitable but automation._SUBJECTS cannot name its subject, so "
            "release_listeners would short-circuit on a null subject and never free the case"
        )


def test_every_subject_source_names_a_param_its_route_still_has() -> None:
    """THE SECOND tripwire, and the one the uid rename got past.

    `test_every_action_trigger_resolves_to_a_live_route` above asks whether the EVENT TYPE still
    matches a route. It does not ask whether the event still carries the field the subject is read
    out of — and when `/firm/contacts/{contact_id}/advisers` became `{contact_uid}`, that is exactly
    what changed. The event kept its name and kept being emitted, so the tripwire stayed green while
    `_SUBJECTS` went on reading a path param that no longer exists.

    Nothing downstream complains, which is why this needs a test rather than a bug report: an
    unresolvable subject is a legitimate state (`resolve_subject` returns None and never raises), so
    the notification simply finds no recipients and the automation simply never fires. Five triggers
    were dead this way — assign an adviser, link a portal login, raise an instruction, change bank
    details, change an account's model.

    The rule is deliberately "AT LEAST ONE of the declared params is live", not "all are": the
    stream is history and is never rewritten, so a pre-rename row is still read by its old
    `path_param` on purpose. What must not happen is EVERY declared name being stale.
    """
    from app.flows.automation import _SUBJECTS

    params_by_event: dict[str, set[str]] = {}
    for route in api_routes(app):
        if not isinstance(route, APIRoute) or not (route.methods & MUTATING_METHODS):
            continue
        params_by_event.setdefault(event_type_for(route), set()).update(
            re.findall(r"{([^}:]+)", route.path)
        )

    stale: list[str] = []
    for event, source in sorted(_SUBJECTS.items()):
        declared = {p for p in (source.path_param, source.sql_param) if p}
        # Only the path-reading strategies are in scope. A source that reads `metadata.subject` or
        # falls back to the DML row names no path param and cannot go stale this way.
        if not declared:
            continue
        live = params_by_event.get(event)
        if live is None:
            continue  # a domain event with no route of its own — the other tripwire's business.
        if not (declared & live):
            stale.append(f"  {event}: declares {sorted(declared)}, route offers {sorted(live)}")

    assert not stale, (
        "These subject sources read a path param their route no longer has, so the event resolves "
        "to no subject — the notification reaches nobody and the automation never fires, both "
        "silently:\n" + "\n".join(stale) + "\n\nFix: add the live param to the entry in "
        "app/flows/automation.py::_SUBJECTS. A uid needs `sql`/`sql_param` (it is a handle, "
        "not the id); keep the old `path_param` beside it so pre-rename rows still resolve."
    )
