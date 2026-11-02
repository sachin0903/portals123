"""Route-resolution smoke tests for the flow/journey HTTP surface (pure, no DB).

Starlette dispatches on the FIRST route whose path *fully* matches, so a literal path segment that
looks like a path parameter is unreachable if a dynamic route is registered ahead of it. That is
exactly how ``GET /flows/dead-letters`` shipped dead: declared after ``GET /flows/{uid}``, every
request landed in ``get_flow`` with ``uid='dead-letters'`` and 404'd, so the operator dead-letter
page rendered its error banner forever.

These tests resolve real paths through the routers the way the ASGI app does, so any future
reordering (or a new ``/flows/{uid}``-shaped route added above a literal one) fails here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from starlette.routing import Match

from app.routers import applications as applications_router
from app.routers import flows as flows_router


def _scope(path: str, method: str = "GET") -> dict[str, Any]:
    return {
        "type": "http",
        "path": path,
        "method": method,
        "path_params": {},
        "headers": [],
        "query_string": b"",
        "root_path": "",
    }


def _resolve(
    router: APIRouter, path: str, method: str = "GET"
) -> tuple[str | None, dict[str, Any]]:
    """The route the ASGI app would dispatch to: first FULL match wins, else first PARTIAL."""
    partial: tuple[str | None, dict[str, Any]] | None = None
    for route in router.routes:
        match, child = route.matches(_scope(path, method))
        name = getattr(route, "name", None)
        if match == Match.FULL:
            return name, dict(child.get("path_params") or {})
        if match == Match.PARTIAL and partial is None:
            partial = (name, dict(child.get("path_params") or {}))
    return partial or (None, {})


# ---- dead letters: the literal paths must win over /flows/{uid} ----
def test_dead_letters_list_is_reachable() -> None:
    name, params = _resolve(flows_router.router, "/flows/dead-letters")
    assert name == "list-dead-letters", (
        f"GET /flows/dead-letters resolves to {name!r} with {params!r} — a dynamic route shadows it"
    )
    assert params == {}


def test_dead_letter_replay_is_reachable() -> None:
    name, _ = _resolve(flows_router.router, "/flows/dead-letters/outbox/12/replay", "POST")
    assert name == "replay-dead-letter"


def test_a_real_flow_uid_still_reaches_get_flow() -> None:
    # Moving the literal routes up must not steal ordinary uids.
    name, params = _resolve(flows_router.router, "/flows/flw_abc123")
    assert name == "get-flow"
    assert params == {"uid": "flw_abc123"}


def test_flow_version_routes_still_resolve() -> None:
    assert _resolve(flows_router.router, "/flows/flw_1/versions")[0] == "list-flow-versions"
    assert _resolve(flows_router.router, "/flows/flw_1/versions/flv_1")[0] == "get-flow-version"
    assert (
        _resolve(flows_router.router, "/flows/flw_1/versions/flv_1/publish", "POST")[0]
        == "publish-flow-version"
    )


# ---- the general rule, so the next literal route can't ship dead ----
def _literal_first_segment_routes(router: APIRouter) -> list[Any]:
    """Routes whose path has no ``{param}`` at all — the shadowable kind."""
    return [r for r in router.routes if "{" not in getattr(r, "path", "{")]


def test_no_literal_route_is_shadowed_by_a_dynamic_one() -> None:
    """Every fully-literal route in these routers must be the first full match for its own path."""
    for router in (flows_router.router, applications_router.router):
        for route in _literal_first_segment_routes(router):
            for method in sorted(getattr(route, "methods", set()) or {"GET"}):
                if method in ("HEAD", "OPTIONS"):
                    continue
                resolved, params = _resolve(router, route.path, method)
                assert resolved == route.name, (
                    f"{method} {route.path} resolves to {resolved!r} ({params!r}), "
                    f"not {route.name!r} — a dynamic route is registered above it"
                )


def test_application_step_form_routes_resolve() -> None:
    r = applications_router.router  # mounted with prefix='/applications'
    assert (
        _resolve(r, "/applications/exec_1/cases/case_1/steps/review/form")[0]
        == "get-application-step-form"
    )
    assert (
        _resolve(r, "/applications/exec_1/cases/case_1/steps/review/form", "POST")[0]
        == "submit-application-step-form"
    )
    assert _resolve(r, "/applications/exec_1/action", "POST")[0] == "action-application-step"
    assert _resolve(r, "/applications/exec_1/retry", "POST")[0] == "retry-application-step"
    assert _resolve(r, "/applications/exec_1/cancel", "POST")[0] == "cancel-application"
    # The client's own onboarding view is a literal that sits among /{exec_uid}-shaped routes.
    assert _resolve(r, "/applications/me")[0] == "my-onboarding"
