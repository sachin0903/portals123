"""The AML control expressed as a flow-engine outcome: what an ``aml_check`` step does when it
starts, and what releases it once a screen has run.

No database and no network. Everything here is a DECISION — raise a task or don't, wait or
release, publish or refuse — so the session is a recorder (``FakeSession``/``FakeResult`` below).
A container would prove Postgres works; this proves the policy holds.

The load-bearing claim of the whole file: **a firm may ask for a screen, only Graphene runs and
clears one.** An ``aml_check`` step opens a pending check and WAITS; the screen is a task in
Graphene's operations queue, never the firm's, and nothing passes the step until a result arrives.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.complycube import provider as complycube_provider
from app.complycube import screen as complycube_screen
from app.complycube import tasks as screening_tasks
from app.config import settings
from app.flows import kyc as kyc_mod
from app.flows import orchestrator, providers
from app.flows import step_handlers as _step_handlers  # noqa: F401 - registers the handlers
from app.flows.dispatcher import PermanentDispatchError
from app.flows.domain_events import StepSignal
from app.flows.handlers import StepContext, get_handler

NOW = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.UTC)

FIRM_TENANT = 5
OPS_TENANT = 900
COMPLIANCE_ROLE_ID = 31
CONTACT_ID = 42
CHECK_ID = 77

API_ROOT = Path(__file__).resolve().parents[1]


class FakeResult:
    """Just enough of a SQLAlchemy Result for the access shapes these code paths use."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one_or_none(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        first = self._rows[0] if self._rows else {}
        return next(iter(first.values()), None)

    def scalar_one_or_none(self) -> Any:
        if not self._rows:
            return None
        return next(iter(self._rows[0].values()), None)


class FakeSession:
    """Records every statement (and its params) so a test can assert what was written.

    Routing is by a distinctive fragment of each statement rather than by call order, so a handler
    that grows an extra read does not silently re-point an existing answer.
    """

    def __init__(
        self,
        *,
        existing_checks: list[dict[str, Any]] | None = None,
        new_check_id: int = CHECK_ID,
        compliance_role_id: int | None = COMPLIANCE_ROLE_ID,
        contact_label: dict[str, Any] | None = None,
        waiting_step: dict[str, Any] | None = None,
    ) -> None:
        self.statements: list[str] = []
        self.params: list[dict[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self._existing_checks = existing_checks or []
        self._new_check_id = new_check_id
        self._compliance_role_id = compliance_role_id
        self._contact_label = contact_label or {"who": "Ada Lovelace", "firm": "Firm A"}
        self._waiting_step = waiting_step

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakeResult:
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        self.params.append(params or {})
        if "INSERT INTO crm.kyc_checks" in sql:
            return FakeResult([{"id": self._new_check_id}])
        if "db_now" in sql:  # ReviewHandler's dedup candidates
            return FakeResult(self._existing_checks)
        if "FROM crm.contacts c JOIN auth.tenants t" in sql:
            return FakeResult([self._contact_label])
        if "FROM auth.roles" in sql:
            role = self._compliance_role_id
            return FakeResult([{"id": role}] if role is not None else [])
        if "FROM flows.case_steps cs JOIN flows.cases c" in sql:
            return FakeResult([self._waiting_step] if self._waiting_step else [])
        if "SELECT now()" in sql:
            return FakeResult([{"now": NOW}])
        return FakeResult([])

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    # ---- assertions helpers ----

    def wrote(self, needle: str) -> bool:
        return any(needle in s for s in self.statements)

    def outbox(self) -> list[dict[str, Any]]:
        """Every row enqueued on ``flows.outbox``, as ``{"event_type": ..., "payload": {...}}``."""
        rows = []
        for sql, params in zip(self.statements, self.params, strict=True):
            if "INSERT INTO flows.outbox" in sql:
                rows.append({"event_type": params["et"], "payload": json.loads(params["p"])})
        return rows

    def params_for(self, needle: str) -> dict[str, Any]:
        for sql, params in zip(self.statements, self.params, strict=True):
            if needle in sql:
                return params
        raise AssertionError(f"no statement matched {needle!r}; ran {self.statements}")


def _ctx(session: FakeSession, **kw: Any) -> StepContext:
    return StepContext(
        session=cast(AsyncSession, session),
        tenant_id=FIRM_TENANT,
        execution_id=3,
        case_id=11,
        step_key="aml",
        config=kw.get("config", {}),
        participant_subject_id=kw.get("participant_subject_id", CONTACT_ID),
        data=kw.get("data", {}),
    )


def _valid_check() -> dict[str, Any]:
    """A currently-valid AML pass for the same person, from an earlier account."""
    return {
        "contact_id": CONTACT_ID,
        "kind": "aml",
        "status": "passed",
        "verified_at": NOW - dt.timedelta(days=10),
        "valid_until": NOW + dt.timedelta(days=200),
        "db_now": NOW,
    }


class Recorder:
    """Captures the keyword arguments of whatever it stands in for."""

    def __init__(self, result: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result

    async def __call__(self, *args: Any, **kw: Any) -> Any:
        self.calls.append({"args": args, **kw})
        return self._result


@pytest.fixture
def ops_queue(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Graphene's operations task inbox: every task raised lands here instead of the database."""
    tasks = Recorder({"uid": "tsk_1"})
    monkeypatch.setattr(screening_tasks.tasks_service, "create_task", tasks)

    async def ops_tenant(_session: Any) -> int:
        return OPS_TENANT

    monkeypatch.setattr(screening_tasks.environments, "operations_tenant_id", ops_tenant)
    return tasks


@pytest.fixture
def reported(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every failure the ComplyCube adapter reports instead of raising."""
    calls: list[dict[str, Any]] = []

    def report(message: str, /, **context: Any) -> None:
        calls.append({"message": message, **context})

    monkeypatch.setattr(complycube_screen, "report_error", report)
    return calls


# ==================================================================================================
# 1. The registry: which adapter a step's `provider` resolves to
# ==================================================================================================

#: What ``providers.known()`` must resolve, as ``"name/step type" -> adapter class``.
_EXPECTED_REGISTRY = {
    "complycube/aml_check": "ComplyCubeAmlProvider",
    "complycube/verify_identity": "ComplyCubeIdentityProvider",
    "creditsafe/aml_check": "ComplyCubeAmlProvider",
    "idenfy/verify_identity": "ComplyCubeIdentityProvider",
}

_RESOLVE_SCRIPT = """
import json
import app.complycube.provider
from app.flows import providers
print(json.dumps({f"{n}/{k}": type(a).__name__ for (n, k), a in providers.known().items()}))
"""


def test_complycube_resolves_for_both_step_types_whatever_the_import_order() -> None:
    """The registry must not depend on which module a process imported first.

    ``app.complycube.provider`` imports ``app.flows``, so a registry built when ``app.flows`` loads
    meets the adapter module half-initialised whenever the adapter is imported first — which
    ruff's alphabetical ordering does on its own. The entry is then missing for the whole process,
    with two silent results: every ComplyCube step becomes a manual gate nobody is told to action,
    and publish preflight refuses the same config as an unknown provider.

    The adapter-first order runs in a fresh interpreter, because this process has imported both
    modules already. The tests below resolve in this process, whatever order it used.
    """
    done = subprocess.run(  # noqa: S603 - a fixed script run by this interpreter
        [sys.executable, "-c", _RESOLVE_SCRIPT],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == _EXPECTED_REGISTRY
    resolved = {f"{n}/{k}": type(a).__name__ for (n, k), a in providers.known().items()}
    assert resolved == _EXPECTED_REGISTRY


def test_complycube_serves_both_step_types_with_one_adapter_each() -> None:
    aml = providers.get_provider("complycube", "aml_check")
    identity = providers.get_provider("complycube", "verify_identity")
    assert isinstance(aml, complycube_provider.ComplyCubeAmlProvider)
    assert isinstance(identity, complycube_provider.ComplyCubeIdentityProvider)
    assert aml.name == identity.name == "complycube"
    assert providers.names("aml_check") >= {"complycube", "manual"}
    assert providers.names("verify_identity") >= {"complycube", "manual"}


def test_the_retired_names_run_on_the_complycube_adapters() -> None:
    """A published version is immutable, so a journey in flight still names ``creditsafe`` or
    ``idenfy``. Each resolves to the ComplyCube adapter for its own step type."""
    assert providers.RETIRED == {"creditsafe", "idenfy"}
    assert isinstance(
        providers.get_provider("creditsafe", "aml_check"), complycube_provider.ComplyCubeAmlProvider
    )
    assert isinstance(
        providers.get_provider("idenfy", "verify_identity"),
        complycube_provider.ComplyCubeIdentityProvider,
    )
    # An alias serves its own step type only: neither name ever ran the other check.
    assert providers.get_provider("creditsafe", "verify_identity").name == "manual"
    assert providers.get_provider("idenfy", "aml_check").name == "manual"


def test_an_unregistered_provider_falls_back_to_the_manual_gate() -> None:
    """A step naming a vendor we cannot run must degrade to staff review rather than raise
    mid-instantiation: an exception here would fail the whole case-advance transaction."""
    assert providers.get_provider("experian").name == "manual"
    assert providers.get_provider("experian", "aml_check").name == "manual"
    assert providers.get_provider("manual", "aml_check").name == "manual"
    assert providers.get_provider(None).name == "manual"


# ==================================================================================================
# 2. The trigger: what an aml_check step does when it is instantiated
# ==================================================================================================


#: Every ``provider`` a flow saved before may carry on its AML step, and none. Each is ignored.
_AML_CONFIGS = [
    {},
    {"provider": "manual"},
    {"provider": "complycube"},
    {"provider": "creditsafe"},
]


@pytest.mark.parametrize("config", _AML_CONFIGS)
@pytest.mark.asyncio
async def test_an_aml_step_opens_a_pending_check_and_queues_its_screen(
    config: dict[str, Any], ops_queue: Recorder
) -> None:
    """THE case. Identity has passed, so the step opens its pending check and queues the client's
    AML screen to run by itself; nobody in operations is asked to press anything. There is no
    manual AML step: a ``provider`` an older flow stored, ``manual`` included, changes nothing.

    The kyc row stays pending and the step reports WAITING; only the screen's result releases it.
    The screen is queued in the step's own transaction, so it runs exactly when the step starts.
    """
    session = FakeSession()
    handler = get_handler("aml_check")
    assert handler is not None

    event = await handler.instantiate(_ctx(session, config=config))

    assert event is not None and event.signal == StepSignal.WAITING
    assert session.wrote("INSERT INTO crm.kyc_checks")
    assert not session.wrote("UPDATE crm.kyc_checks"), "the step recorded an outcome by itself"
    # The step records the check it opened, so the result settles this step and no other.
    assert json.loads(session.params_for("UPDATE flows.case_steps")["d"]) == {
        "kyc_check_id": CHECK_ID
    }
    assert session.outbox() == [
        {
            "event_type": complycube_screen.EVENT,
            "payload": {"contact_id": CONTACT_ID, "kyc_check_id": CHECK_ID},
        }
    ]
    assert ops_queue.calls == []
    assert session.commits == 0
    assert session.rollbacks == 0


@pytest.mark.parametrize("config", _AML_CONFIGS)
@pytest.mark.asyncio
async def test_the_check_records_the_provider_that_runs_it(
    config: dict[str, Any], ops_queue: Recorder
) -> None:
    """``crm.kyc_checks.provider`` names who actually screens: ComplyCube, whatever an older
    version of the flow says."""
    session = FakeSession()
    handler = get_handler("aml_check")
    assert handler is not None

    await handler.instantiate(_ctx(session, config=config))

    assert session.params_for("INSERT INTO crm.kyc_checks")["p"] == "complycube"


@pytest.mark.asyncio
async def test_dedup_short_circuits_before_any_task(ops_queue: Recorder) -> None:
    """A person already holding a valid AML pass is not screened again.

    A screen is billed per search, so a second account for an existing client must reuse the pass
    rather than buy a duplicate. The step completes immediately, so there is nothing left to ask
    for — a task here would screen someone whose step has already passed.
    """
    session = FakeSession(existing_checks=[_valid_check()])
    handler = get_handler("aml_check")
    assert handler is not None

    event = await handler.instantiate(_ctx(session, config={"provider": "complycube"}))

    assert event is not None and event.signal == StepSignal.COMPLETED
    assert event.reason == "already_verified"
    assert not session.wrote("INSERT INTO crm.kyc_checks")
    assert ops_queue.calls == []


@pytest.mark.asyncio
async def test_a_screen_with_no_subject_queues_nothing(ops_queue: Recorder) -> None:
    """A screen needs somebody to screen. The step waits, and nothing is queued or raised."""
    session = FakeSession()
    handler = get_handler("aml_check")
    assert handler is not None

    event = await handler.instantiate(
        _ctx(session, config={"provider": "complycube"}, participant_subject_id=None)
    )

    assert event is not None and event.signal == StepSignal.WAITING
    assert not session.wrote("INSERT INTO crm.kyc_checks")
    assert session.outbox() == []
    assert ops_queue.calls == []

    # The adapter holds the same line when it is called directly, with or without a session.
    for target in (session, None):
        await complycube_provider.ComplyCubeAmlProvider().start(
            tenant_id=FIRM_TENANT,
            case_id=11,
            step_id="aml",
            subject_contact_id=None,
            session=target,
            kyc_check_id=CHECK_ID,
        )
    assert session.outbox() == []
    assert ops_queue.calls == []


@pytest.mark.asyncio
async def test_a_screen_that_could_not_run_asks_operations_and_says_why(
    ops_queue: Recorder,
) -> None:
    """The fallback: one task in the OPERATIONS tenant, assigned to its compliance role. Raised in
    the firm's tenant it would put "possible sanctions match" about the firm's own client in the
    firm's own queue, which is both a tipping-off risk and useless, since a firm cannot screen."""
    session = FakeSession()

    await complycube_screen.raise_screen_task(
        cast(AsyncSession, session),
        tenant_id=FIRM_TENANT,
        contact_id=CONTACT_ID,
        kyc_check_id=CHECK_ID,
        reason="ComplyCube is not configured",
    )

    [task] = ops_queue.calls
    assert task["tenant_id"] == OPS_TENANT
    assert task["assignee_kind"] == "role"
    assert task["assignee_role_id"] == COMPLIANCE_ROLE_ID
    assert task["category"] == "aml"
    assert task["title"] == "Screen Ada Lovelace"
    assert "ComplyCube is not configured" in task["description"]
    assert task["source_ref"] == f"screen:{CONTACT_ID}:{CHECK_ID}"
    assert task["source_ref"] == complycube_screen.screen_task_ref(CONTACT_ID, CHECK_ID)


@pytest.mark.asyncio
async def test_a_missing_operations_tenant_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, ops_queue: Recorder, reported: list[dict[str, Any]]
) -> None:
    """There is nowhere to put the task, and raising would roll back the step with it. The client
    still shows in the console as due a screen, so the screen is not lost; the gap is reported."""

    async def unregistered(_session: Any) -> int:
        raise LookupError("the core WorkOS environment is not registered")

    monkeypatch.setattr(screening_tasks.environments, "operations_tenant_id", unregistered)
    session = FakeSession()

    await complycube_screen.raise_screen_task(
        cast(AsyncSession, session),
        tenant_id=FIRM_TENANT,
        contact_id=CONTACT_ID,
        kyc_check_id=CHECK_ID,
        reason="ComplyCube is not configured",
    )

    assert ops_queue.calls == []
    assert len(reported) == 1
    assert reported[0]["event"] == "complycube_screen_task_failed"
    assert isinstance(reported[0]["exc"], PermanentDispatchError)
    assert "operations tenant" in str(reported[0]["exc"])


@pytest.mark.asyncio
async def test_a_missing_compliance_role_is_reported_not_raised(
    ops_queue: Recorder, reported: list[dict[str, Any]]
) -> None:
    """An unassignable task is worse than none: it would sit in the operations inbox owned by
    nobody. Only a human can create the role, so the gap is reported rather than retried."""
    session = FakeSession(compliance_role_id=None)

    await complycube_screen.raise_screen_task(
        cast(AsyncSession, session),
        tenant_id=FIRM_TENANT,
        contact_id=CONTACT_ID,
        kyc_check_id=CHECK_ID,
        reason="ComplyCube is not configured",
    )

    assert ops_queue.calls == []
    assert len(reported) == 1
    assert "compliance" in str(reported[0]["exc"])


# ==================================================================================================
# 3. Closing the loop: on_aml_result
# ==================================================================================================


def _waiting_step() -> dict[str, Any]:
    return {
        "case_id": 11,
        "step_key": "aml",
        "participant_subject_id": CONTACT_ID,
        "tenant_id": FIRM_TENANT,
    }


@pytest.mark.asyncio
async def test_a_pass_stands_with_no_horizon(monkeypatch: pytest.MonkeyPatch) -> None:
    """An AML pass does not lapse: it is stamped with no ``valid_until``, and ``kyc.is_valid`` reads
    that as a pass that stands, so every later account reuses it rather than paying for a second
    screen. Ongoing monitoring keeps it current."""
    applied = Recorder()
    monkeypatch.setattr(orchestrator, "apply_step_event", applied)
    session = FakeSession(waiting_step=_waiting_step())

    released = await orchestrator.on_aml_result(
        cast(AsyncSession, session), CHECK_ID, outcome="passed"
    )

    assert released is True
    params = session.params_for("UPDATE crm.kyc_checks SET status")
    assert params["st"] == "passed"
    assert params["vu"] is None
    assert kyc_mod.is_valid(kyc_mod.KycCheck(1, kyc_mod.KIND_AML, "passed"), NOW)
    assert params["verified"] == NOW
    # The step is released only after the record agrees, so the client record and the engine cannot
    # disagree about whether the person passed.
    assert len(applied.calls) == 1
    assert applied.calls[0]["args"][2].signal == StepSignal.COMPLETED


@pytest.mark.asyncio
async def test_a_failure_stamps_no_validity_and_fails_the_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction of the same field: a referred or failed check must not become reusable,
    or a confirmed match would satisfy the next account's dedup."""
    applied = Recorder()
    monkeypatch.setattr(orchestrator, "apply_step_event", applied)
    session = FakeSession(waiting_step=_waiting_step())

    released = await orchestrator.on_aml_result(
        cast(AsyncSession, session), CHECK_ID, outcome="failed"
    )

    assert released is True
    params = session.params_for("UPDATE crm.kyc_checks SET status")
    assert params["st"] == "failed"
    assert params["vu"] is None
    assert params["verified"] is None
    assert applied.calls[0]["args"][2].signal == StepSignal.FAILED


@pytest.mark.asyncio
async def test_an_ad_hoc_console_screen_releases_nobodys_onboarding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator screening someone from the AML console owns no case step.

    Its check id matches nothing waiting, and the write must be skipped entirely rather than
    falling through to "the nearest waiting step" — which is how a curiosity search about one client
    would advance a different client's journey. The caller is told nothing was released.
    """
    applied = Recorder()
    monkeypatch.setattr(orchestrator, "apply_step_event", applied)
    session = FakeSession(waiting_step=None)

    released = await orchestrator.on_aml_result(cast(AsyncSession, session), 9999, outcome="passed")

    assert released is False
    assert applied.calls == []
    assert not session.wrote("UPDATE crm.kyc_checks")

    # ...and the same for a caller that has no check id at all.
    bare = FakeSession(waiting_step=_waiting_step())
    released = await orchestrator.on_aml_result(cast(AsyncSession, bare), None, outcome="passed")
    assert released is False
    assert applied.calls == []
    assert bare.statements == []


# ==================================================================================================
# 4. Publish-time preflight
# ==================================================================================================


@pytest.mark.asyncio
async def test_preflight_never_blocks_an_aml_step() -> None:
    """An AML step has no provider to get wrong, and publishes even with no ComplyCube key: the
    worker then hands the screen to operations as a task. A ``provider`` an older flow carries is
    ignored, a retired or unknown one included."""
    assert settings.complycube_api_key == ""  # conftest blanks it
    handler = get_handler("aml_check")
    assert handler is not None
    session = cast(AsyncSession, None)

    for config in [*_AML_CONFIGS, {"provider": "experian"}]:
        assert await handler.preflight(session, FIRM_TENANT, config) == []


#: The step types whose ``provider`` an author picks.
_STEP_TYPES = ("verify_identity",)


@pytest.mark.parametrize("step_type", _STEP_TYPES)
@pytest.mark.asyncio
async def test_preflight_refuses_a_retired_or_unknown_provider(step_type: str) -> None:
    """A bad ``provider`` has to be caught at publish, not mid-onboarding on a real client.

    A retired name still RUNS on a version already published, but a new flow may not name it. The
    refusal names the author's own value and what IS available, so the fix is obvious.
    """
    handler = get_handler(step_type)
    assert handler is not None
    session = cast(AsyncSession, None)  # these branches never touch the session

    for name in ("creditsafe", "idenfy", "experian"):
        issues = await handler.preflight(session, FIRM_TENANT, {"provider": name})
        assert issues == [
            f"“{name}” is not an available check service. Choose one of: complycube, manual."
        ]


@pytest.mark.parametrize("step_type", _STEP_TYPES)
@pytest.mark.asyncio
async def test_preflight_refuses_complycube_without_its_key(
    step_type: str, caplog: pytest.LogCaptureFixture
) -> None:
    """With no ComplyCube key the check fails at RUNTIME, on a real client, so publish refuses it.

    The author is told to contact support; the SETTING is ours and is logged instead, because a
    firm cannot act on it and a publish dialog is not the place to name one.
    """
    assert settings.complycube_api_key == ""  # conftest blanks it
    handler = get_handler(step_type)
    assert handler is not None

    with caplog.at_level(logging.WARNING):
        issues = await handler.preflight(
            cast(AsyncSession, None), FIRM_TENANT, {"provider": "complycube"}
        )

    assert issues == ["ComplyCube is unavailable. Contact support before publishing this journey."]
    assert "COMPLYCUBE" not in issues[0]
    assert "COMPLYCUBE_API_KEY" in caplog.text


@pytest.mark.parametrize("step_type", _STEP_TYPES)
@pytest.mark.asyncio
async def test_preflight_passes_a_configured_complycube_step(
    step_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control for the case above: with the key set the same config publishes.
    Without it, a preflight that refused complycube unconditionally would still look correct."""
    monkeypatch.setattr(settings, "complycube_api_key", "k")
    handler = get_handler(step_type)
    assert handler is not None

    issues = await handler.preflight(
        cast(AsyncSession, None), FIRM_TENANT, {"provider": "complycube"}
    )

    assert issues == []


@pytest.mark.parametrize("step_type", _STEP_TYPES)
@pytest.mark.asyncio
async def test_preflight_never_blocks_a_manual_step(step_type: str) -> None:
    """``manual`` is the staff-review gate: nothing external to check, so it publishes whatever
    this deployment has configured."""
    handler = get_handler(step_type)
    assert handler is not None
    session = cast(AsyncSession, None)

    assert await handler.preflight(session, FIRM_TENANT, {"provider": "manual"}) == []
    assert await handler.preflight(session, FIRM_TENANT, {}) == []
