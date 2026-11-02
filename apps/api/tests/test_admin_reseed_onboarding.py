"""The TEMPORARY onboarding-reseed route (`admin.admin_reseed_onboarding_flow`).

The route exists because a firm is seeded exactly once, at creation, so one created before a seed
change is frozen at the definition that shipped that day. Re-running the seed is safe for journeys
in flight -- it is expand-only and a running journey is pinned to the version it started on -- and
UNSAFE for firm-authored edits to the six seeded forms, which `_write_form_fields` replaces from
the hard-coded list.

That asymmetry is the whole design, so these pin the guards that express it rather than the happy
path (which `test_flows_onboarding_seed.py` already covers through `seed_onboarding` itself). They
are deliberately DB-free: every claim here is about what the route refuses and what it refuses to
reach for, and a testcontainer would only slow that down.

Delete this file with the route.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi import HTTPException

from app.auth import Principal
from app.routers import admin as admin_api

ROUTE_SRC = inspect.getsource(admin_api.admin_reseed_onboarding_flow)


def _code_only(source: str) -> str:
    """The route MINUS its docstring and comments.

    The prose legitimately names both `DELETE` and `_all_publish_issues` while explaining why
    neither is used, so a scan over the whole source fails on the explanation rather than on the
    thing it warns about -- which would train the next reader to delete the warning.
    """
    body = source.split('"""', 2)[2] if source.count('"""') >= 2 else source
    kept = [line for line in body.splitlines() if not line.strip().startswith("#")]
    return "\n".join(kept)


ROUTE_CODE = _code_only(ROUTE_SRC)


class TestItIsNotOfferedInProduction:
    """The form-fields rewrite is why. `app_stage` defaults to 'production' (config.py), so an
    unconfigured deployment refuses -- the right direction for a temporary tool."""

    async def test_production_refuses_before_touching_the_tenant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scoped: list[object] = []

        async def _scope(*args: Any, **kwargs: Any) -> None:
            scoped.append(args)

        monkeypatch.setattr(admin_api, "_require_tenant_scope", _scope)
        monkeypatch.setattr(admin_api.settings, "app_stage", "production")

        with pytest.raises(HTTPException) as caught:
            await admin_api.admin_reseed_onboarding_flow(
                tenant_id=1,
                session=None,  # type: ignore[arg-type]
                principal=Principal(user_id="u_ops", organisation_id="org_ops"),
            )
        assert caught.value.status_code == 403
        # The refusal names the stage rather than reading as a permission problem: an operator on
        # staging seeing "403" needs to know WHICH guard said no.
        assert "APP_STAGE" in str(caught.value.detail)

    @pytest.mark.parametrize("stage", ["dev", "staging"])
    async def test_a_development_stage_gets_past_the_guard(
        self, monkeypatch: pytest.MonkeyPatch, stage: str
    ) -> None:
        """Past the stage guard it must reach real work -- proven by failing on the absent
        session rather than by raising the 403 again."""

        async def _scope(*args: Any, **kwargs: Any) -> None:
            return None

        monkeypatch.setattr(admin_api, "_require_tenant_scope", _scope)
        monkeypatch.setattr(admin_api.settings, "app_stage", stage)

        with pytest.raises(Exception) as caught:
            await admin_api.admin_reseed_onboarding_flow(
                tenant_id=1,
                session=None,  # type: ignore[arg-type]
                principal=Principal(user_id="u_ops", organisation_id="org_ops"),
            )
        assert not (isinstance(caught.value, HTTPException) and caught.value.status_code == 403), (
            f"{stage} was refused as a production stage"
        )


class TestItCannotReachTheDestructivePath:
    """`scripts/seed_onboarding.py --reset` DELETEs the flow, its forms and its terms.

    `flows.cases` and `flows.executions` cascade from `flows.flows`, so that path would take a
    firm's entire onboarding history with it. It stays a script for a tenant with nothing in
    flight; a source scan is the only thing that stops it being 'just added' to the route later,
    the same trick `test_email_default_refresh.py` uses to keep `force=` off the email route.
    """

    def test_the_route_issues_no_deletes(self) -> None:
        assert "DELETE" not in ROUTE_CODE.upper(), (
            "the reseed route must never delete: it is expand-only, and cases/executions cascade "
            "from flows.flows"
        )

    def test_the_route_goes_through_the_one_seed_function(self) -> None:
        """Not a hand-rolled second publish path. `_republish` is what makes it expand-only and
        content-idempotent; a copy would drift from it."""
        assert "seed_onboarding(session, tenant_id=tenant_id)" in ROUTE_CODE

    def test_the_seed_is_preflighted_inside_a_savepoint(self) -> None:
        """The seed publishes without the DB-backed publish checks, so it can install a definition
        the builder would then refuse to republish -- a trap an operator cannot see or fix from the
        UI. The savepoint is what lets this refuse instead of leaving that behind."""
        assert "session.begin_nested()" in ROUTE_CODE
        for check in ("_reference_issues", "_handler_issues", "_automation_issues"):
            assert check in ROUTE_CODE, f"{check} is not run before the reseed is kept"

    def test_the_tenant_is_passed_explicitly_not_read_off_the_claim(self) -> None:
        """`flows._all_publish_issues` derives the tenant from `auth.tenant_id()`. On the admin
        surface that is the OPERATOR's tenant, so using it would check the wrong firm's forms and
        pass for the wrong reason."""
        assert "_all_publish_issues" not in ROUTE_CODE
        assert "tenant_id, definition" in ROUTE_CODE
