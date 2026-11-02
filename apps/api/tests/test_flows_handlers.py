"""Unit tests for the step-handler registry + the handlers' pure decision logic (no DB)."""

import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.flows import step_handlers  # noqa: F401 - registers handlers on import
from app.flows.domain_events import StepSignal
from app.flows.handlers import StepContext, get_handler, registered_types


def _ctx(step_key: str = "s", **kw: object) -> StepContext:
    return StepContext(
        session=cast(AsyncSession, kw.get("session", None)),
        tenant_id=1,
        execution_id=int(cast(Any, kw.get("execution_id", 1))),
        case_id=1,
        step_key=step_key,
        config=cast(dict, kw.get("config", {})),
        participant_subject_id=cast("int | None", kw.get("participant_subject_id")),
        subject_type=cast("str | None", kw.get("subject_type")),
        subject_id=cast("int | None", kw.get("subject_id")),
        request_id=cast("int | None", kw.get("request_id")),
    )


class _Rows:
    """Minimal stand-in for ``session.execute(...).mappings().all()``."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


def test_all_step_types_registered() -> None:
    assert registered_types() >= {
        "task",
        "verify_identity",
        "aml_check",
        "fill_form",
        "accept_terms",
        "upload_document",
        "generate_document",
        "setup_account",
    }


def test_unknown_type_returns_none() -> None:
    assert get_handler("nope") is None


@pytest.mark.asyncio
async def test_task_instantiate_is_pending() -> None:
    h = get_handler("task")
    assert h is not None
    assert await h.instantiate(_ctx()) is None  # nothing to create; completed via apply_step_event


@pytest.mark.asyncio
async def test_task_without_invite_clients_just_completes() -> None:
    h = get_handler("task")
    assert h is not None
    event = await h.handle(_ctx(config={"description": "done"}), {})
    assert event.signal == StepSignal.COMPLETED
    assert event.reason is None
    assert event.detail == {}


@pytest.mark.asyncio
async def test_invitation_handoff_requires_the_adviser_declaration() -> None:
    h = get_handler("task")
    assert h is not None
    session = AsyncMock()
    with pytest.raises(step_handlers.StepTransitionRefused, match="correct, complete"):
        await h.handle(_ctx(session=session, config={"invite_clients": True}), {})
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_invite_clients_invites_kyc_relevant_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``invite_clients`` is the declare → email handoff: each KYC-relevant owner is provisioned."""
    h = get_handler("task")
    assert h is not None

    contacts = [
        {
            "id": 10,
            "email": "a@example.com",
            "first_name": "Ada",
            "surname": "Lovelace",
            "type": "individual",
        },
        {
            "id": 11,
            "email": "b@example.com",
            "first_name": "Ben",
            "surname": "Bitdiddle",
            "type": "individual",
        },
    ]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Rows(contacts))

    calls: list[dict[str, object]] = []

    async def _fake_provision(session: object, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"contact_id": kwargs["contact_id"]}

    monkeypatch.setattr("app.provisioning.provision_client_login", _fake_provision)

    event = await h.handle(
        _ctx(session=session, execution_id=42, config={"invite_clients": True}),
        {"adviser_confirmed": True},
    )
    assert event.signal == StepSignal.COMPLETED
    assert event.reason == "invited=2 skipped=0 failed=0"
    assert event.detail == {"invited": 2, "skipped": 0, "failed": 0}
    assert [c["contact_id"] for c in calls] == [10, 11]
    assert all(c["email"] in {"a@example.com", "b@example.com"} for c in calls)
    assert all(c["role_slug"] == "client" for c in calls)
    declaration_stmt = session.execute.await_args_list[0].args[0]
    assert "adviser_declaration = true" in str(declaration_stmt)
    assert "adviser_declaration_by = auth.uid()" in str(declaration_stmt)
    # The cohort query is scoped to this execution's application (via crm_application_id).
    assert session.execute.await_count == 2
    _stmt, params = session.execute.await_args.args[0], session.execute.await_args.args[1]
    assert params["e"] == 42
    assert params["t"] == 1
    # `registered_contact` is the JISA adult: the child owns the account and cannot be invited,
    # so the invite has to reach the parent or the journey stalls with nobody to declare.
    assert set(params["roles"]) == {"owner", "decision_maker", "trustee", "registered_contact"}
    assert "crm.account_contacts" in str(_stmt)


@pytest.mark.asyncio
async def test_task_invite_clients_skips_missing_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A KYC-relevant owner with no email is skipped — local declaration still completes."""
    h = get_handler("task")
    assert h is not None

    contacts = [
        {"id": 10, "email": None, "first_name": "No", "surname": "Mail", "type": "individual"},
        {"id": 11, "email": "  ", "first_name": "Blank", "surname": "Mail", "type": "individual"},
        {
            "id": 12,
            "email": "ok@example.com",
            "first_name": "Ok",
            "surname": "Mail",
            "type": "individual",
        },
    ]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Rows(contacts))

    calls: list[dict[str, object]] = []

    async def _fake_provision(session: object, **kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"contact_id": kwargs["contact_id"]}

    monkeypatch.setattr("app.provisioning.provision_client_login", _fake_provision)

    event = await h.handle(
        _ctx(session=session, config={"invite_clients": True}), {"adviser_confirmed": True}
    )
    assert event.signal == StepSignal.COMPLETED
    assert event.detail == {"invited": 1, "skipped": 2, "failed": 0}
    assert [c["contact_id"] for c in calls] == [12]


@pytest.mark.asyncio
async def test_task_invite_clients_survives_provision_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WorkOS being down must not fail the step — the declaration is local truth."""
    h = get_handler("task")
    assert h is not None

    contacts = [
        {
            "id": 10,
            "email": "a@example.com",
            "first_name": "A",
            "surname": "A",
            "type": "individual",
        },
        {
            "id": 11,
            "email": "b@example.com",
            "first_name": "B",
            "surname": "B",
            "type": "individual",
        },
    ]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_Rows(contacts))

    async def _boom(session: object, **kwargs: object) -> dict[str, object]:
        if kwargs["contact_id"] == 10:
            raise RuntimeError("workos down")
        return {"contact_id": kwargs["contact_id"]}

    monkeypatch.setattr("app.provisioning.provision_client_login", _boom)

    event = await h.handle(
        _ctx(session=session, config={"invite_clients": True}), {"adviser_confirmed": True}
    )
    assert event.signal == StepSignal.COMPLETED
    assert event.detail == {"invited": 1, "skipped": 0, "failed": 1}


@pytest.mark.asyncio
async def test_review_waits_then_passes_or_fails() -> None:
    h = get_handler("verify_identity")
    assert h is not None
    started = await h.instantiate(_ctx())
    assert started is not None and started.signal == StepSignal.WAITING

    passed = await h.handle(_ctx(), {"outcome": "passed"})
    assert passed.signal == StepSignal.COMPLETED

    referred = await h.handle(_ctx(), {"outcome": "referred"})
    assert referred.signal == StepSignal.FAILED
    assert referred.reason == "review_referred"


def test_config_keys_exposed() -> None:
    assert get_handler("fill_form").config_keys() >= {"form_uid", "mapping"}  # type: ignore[union-attr]
    assert get_handler("setup_account").config_keys() >= {"provider"}  # type: ignore[union-attr]
    assert get_handler("task").config_keys() == {"description", "invite_clients"}  # type: ignore[union-attr]
    assert get_handler("accept_terms").config_keys() >= {"document_uid", "content"}  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_accept_terms_preflight_accepts_written_copy_or_a_pdf() -> None:
    h = get_handler("accept_terms")
    assert h is not None
    session = cast(AsyncSession, None)
    assert await h.preflight(session, 1, {}) == [
        "Choose a library term, write the declaration, or choose a PDF to agree to."
    ]
    assert await h.preflight(session, 1, {"content": "I agree."}) == []
    assert await h.preflight(session, 1, {"document_uid": "d1"}) == []
    assert await h.preflight(session, 1, {"term_uid": "t1"}) == []


@pytest.mark.asyncio
async def test_accept_terms_skips_when_current_version_already_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = get_handler("accept_terms")
    assert h is not None
    monkeypatch.setattr(
        "app.flows.step_handlers.terms_svc.has_accepted_current",
        AsyncMock(return_value=True),
    )
    recorded = AsyncMock()
    monkeypatch.setattr("app.flows.step_handlers._record_terms_acceptance", recorded)
    event = await h.instantiate(
        _ctx(request_id=3, participant_subject_id=9, config={"term_uid": "t1"})
    )
    assert event is not None
    assert event.signal == StepSignal.COMPLETED
    assert event.reason == "already_accepted"
    recorded.assert_awaited_once()


@pytest.mark.asyncio
async def test_accept_terms_writes_in_person_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    h = get_handler("accept_terms")
    assert h is not None
    monkeypatch.setattr(
        "app.flows.step_handlers.terms_svc.get_term",
        AsyncMock(return_value={"current_published_version_id": 4}),
    )
    accept = AsyncMock()
    monkeypatch.setattr("app.flows.step_handlers.terms_svc.accept_term", accept)
    monkeypatch.setattr("app.flows.step_handlers._record_terms_acceptance", AsyncMock())
    event = await h.handle(
        _ctx(request_id=3, participant_subject_id=9, config={"term_uid": "t1"}),
        {
            "in_person": True,
            "attested_by": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "evidence_document_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        },
    )
    assert event.signal == StepSignal.COMPLETED
    assert event.detail == {"catalogue_term_uid": "t1"}
    assert accept.await_args is not None
    assert accept.await_args.kwargs["channel"] == "in_person"
    assert accept.await_args.kwargs["attested_by"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    # The signed copy the adviser uploaded reaches the register row.
    assert (
        accept.await_args.kwargs["evidence_document_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    )


@pytest.mark.asyncio
async def test_a_portal_acceptance_carries_no_evidence_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client's own click is its own evidence; the register row points at nothing."""
    h = get_handler("accept_terms")
    assert h is not None
    monkeypatch.setattr(
        "app.flows.step_handlers.terms_svc.get_term",
        AsyncMock(return_value={"current_published_version_id": 4}),
    )
    accept = AsyncMock()
    monkeypatch.setattr("app.flows.step_handlers.terms_svc.accept_term", accept)
    monkeypatch.setattr("app.flows.step_handlers._record_terms_acceptance", AsyncMock())
    await h.handle(_ctx(request_id=3, participant_subject_id=9, config={"term_uid": "t1"}), {})
    assert accept.await_args is not None
    assert accept.await_args.kwargs["channel"] == "portal"
    assert accept.await_args.kwargs["evidence_document_id"] is None


@pytest.mark.asyncio
async def test_written_declaration_is_filed_as_a_terms_document() -> None:
    h = get_handler("accept_terms")
    assert h is not None
    captured: list[dict[str, object]] = []

    class _Insert:
        def scalar_one(self) -> int:
            return 9

    async def _exec(query: object, params: object = None) -> object:
        if isinstance(params, dict):
            captured.append(params)
        return _Insert()

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_exec)
    event = await h.instantiate(
        _ctx(
            session=session,
            request_id=3,
            config={"content": "I **agree**.", "title": "General declaration"},
        )
    )
    assert event is None
    filed = next(p for p in captured if p.get("n") == "General declaration")
    meta = json.loads(str(filed["m"]))
    assert "<strong>agree</strong>" in meta["content"]
    assert "<script" not in meta["content"]


_PDF_TERM = {
    "uid": "trm_pdf",
    "title": "Platform terms",
    "status": "published",
    "body_kind": "pdf",
    "published_body_kind": "pdf",
    "published_body_html": None,
    "published_file_key": "terms/trm_pdf/body.pdf",
    "published_file_content_type": "application/pdf",
    "current_version_uid": "tv_1",
    "current_published_version_id": 4,
}


@pytest.mark.asyncio
async def test_a_pdf_catalogue_term_is_filed_so_the_client_can_open_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A catalogue term authored as a PDF used to raise an acknowledge item with NO document: the
    client's onboarding dialog looked the item up, found nothing to show and said the document
    could not be loaded. The published file is filed as a ``terms`` document — a ``documents.files``
    row naming the version's own object in the tenant bucket — and the item points at it."""
    h = get_handler("accept_terms")
    assert h is not None
    monkeypatch.setattr(
        "app.flows.step_handlers.terms_svc.has_accepted_current", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        "app.flows.step_handlers.terms_svc.get_term", AsyncMock(return_value=_PDF_TERM)
    )

    class _Store:
        bucket = "t-abc"

    monkeypatch.setattr(
        "app.flows.step_handlers.tenant_storage.get_tenant_store",
        AsyncMock(return_value=_Store()),
    )
    fetched = AsyncMock(return_value=(b"%PDF-1.7 fake", "application/pdf"))
    monkeypatch.setattr("app.flows.step_handlers.storage.get_tenant_object", fetched)
    create_item = AsyncMock(return_value=77)
    monkeypatch.setattr("app.flows.step_handlers._create_request_item", create_item)

    captured: list[tuple[str, dict[str, object]]] = []

    class _Result:
        def __init__(self, value: object) -> None:
            self._value = value

        def scalar_one_or_none(self) -> object:
            return self._value

        def scalar_one(self) -> object:
            return self._value

    async def _exec(query: object, params: object = None) -> object:
        sql = str(query)
        if isinstance(params, dict):
            captured.append((sql, params))
        if "SELECT id FROM documents.files" in sql:
            return _Result(None)  # not filed yet → the bytes are fetched and the file row made
        if "INSERT INTO documents.files" in sql:
            return _Result("11111111-1111-1111-1111-111111111111")
        return _Result(None)

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_exec)
    event = await h.instantiate(
        _ctx(
            session=session, request_id=3, participant_subject_id=9, config={"term_uid": "trm_pdf"}
        )
    )
    assert event is None, event

    fetched.assert_awaited_once()
    assert fetched.await_args is not None
    assert fetched.await_args.args[1] == "terms/trm_pdf/body.pdf"
    file_row = next(p for sql, p in captured if "INSERT INTO documents.files" in sql)
    assert file_row["b"] == "t-abc"
    assert file_row["k"] == "terms/trm_pdf/body.pdf"
    assert file_row["ct"] == "application/pdf"
    assert file_row["size"] == len(b"%PDF-1.7 fake")
    doc_row = next(p for sql, p in captured if "INSERT INTO documents.documents" in sql)
    assert doc_row["f"] == "11111111-1111-1111-1111-111111111111"
    assert json.loads(str(doc_row["m"])) == {
        "audience": "client",
        "term_uid": "trm_pdf",
        "term_version_uid": "tv_1",
    }
    # The request item is raised against THAT document — the client's `/terms` lookup finds it.
    assert create_item.await_args is not None
    assert create_item.await_args.kwargs["document_uid"] == doc_row["u"]


@pytest.mark.asyncio
async def test_a_pdf_term_already_filed_is_not_fetched_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second client asked to accept the same version reuses the file row (one per object,
    ``UNIQUE (bucket, storage_key)``) — no round trip to the object store."""
    h = get_handler("accept_terms")
    assert h is not None
    monkeypatch.setattr(
        "app.flows.step_handlers.terms_svc.has_accepted_current", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        "app.flows.step_handlers.terms_svc.get_term", AsyncMock(return_value=_PDF_TERM)
    )

    class _Store:
        bucket = "t-abc"

    monkeypatch.setattr(
        "app.flows.step_handlers.tenant_storage.get_tenant_store",
        AsyncMock(return_value=_Store()),
    )
    fetched = AsyncMock()
    monkeypatch.setattr("app.flows.step_handlers.storage.get_tenant_object", fetched)
    create_item = AsyncMock(return_value=77)
    monkeypatch.setattr("app.flows.step_handlers._create_request_item", create_item)

    class _Result:
        def scalar_one_or_none(self) -> object:
            return "22222222-2222-2222-2222-222222222222"

    async def _exec(query: object, params: object = None) -> object:
        return _Result()

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_exec)
    event = await h.instantiate(
        _ctx(
            session=session, request_id=3, participant_subject_id=9, config={"term_uid": "trm_pdf"}
        )
    )
    assert event is None
    fetched.assert_not_awaited()
    assert create_item.await_args is not None
    assert create_item.await_args.kwargs["document_uid"] is not None


@pytest.mark.asyncio
async def test_a_pdf_term_holds_the_step_when_the_firm_has_no_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Better a step held with a reason than an acknowledgement of a document nobody can open."""
    h = get_handler("accept_terms")
    assert h is not None
    monkeypatch.setattr(
        "app.flows.step_handlers.terms_svc.has_accepted_current", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        "app.flows.step_handlers.terms_svc.get_term", AsyncMock(return_value=_PDF_TERM)
    )
    monkeypatch.setattr(
        "app.flows.step_handlers.tenant_storage.get_tenant_store", AsyncMock(return_value=None)
    )
    create_item = AsyncMock()
    monkeypatch.setattr("app.flows.step_handlers._create_request_item", create_item)
    event = await h.instantiate(
        _ctx(session=AsyncMock(), request_id=3, participant_subject_id=9, config={"term_uid": "t"})
    )
    assert event is not None
    assert event.signal == StepSignal.BLOCKED
    assert event.reason == "term_file_unavailable"
    create_item.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_account_activates_draft_locally() -> None:
    """Local hand-off: draft → active, custodian_open pending_ops marker on accounts.data,
    applications.synced stamped. Custodian enrolment is not called; the scheme-administrator
    sync is best-effort and skipped when the session cannot load the account."""
    h = get_handler("setup_account")
    assert h is not None
    executed: list[str] = []
    bound: list[object] = []

    async def _exec(query: object, params: object = None) -> object:
        executed.append(str(query))
        bound.append(params)
        return None

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_exec)
    event = await h.handle(
        _ctx(
            session=session,
            subject_type="account",
            subject_id=42,
            execution_id=7,
        ),
        {},
    )
    assert event.signal == StepSignal.COMPLETED
    assert event.reason == "account_activated"
    assert event.detail["local_activated"] is True
    assert event.detail["custodian_open"] == "pending_ops"
    assert event.detail["platform_sync"]["administrator"]["status"] == "skipped"
    joined = " ".join(executed)
    assert "platform.accounts" in joined
    assert "active" in joined
    assert "custodian_open" in joined or any(
        isinstance(p, dict) and "marker" in p and "custodian_open" in str(p.get("marker"))
        for p in bound
    )
    assert "crm.applications" in joined
    assert "synced" in joined
    marker_params = next(
        (p for p in bound if isinstance(p, dict) and "marker" in p),
        None,
    )
    assert marker_params is not None
    marker = json.loads(str(marker_params["marker"]))
    assert marker["custodian_open"]["status"] == "pending_ops"
    assert marker["custodian_open"]["local_activated"] is True
    assert "requested_at" in marker["custodian_open"]


@pytest.mark.asyncio
async def test_setup_account_enrols_the_scheme_administrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The journey is how a SIPP is created; minting at Morgan Lloyd cannot wait for a
    later 'Sync to Morgan Lloyd' click. The handler still completes when the enrol is a
    report rather than a raise — missing contact fields are common at this step."""
    seen: dict[str, object] = {}

    async def _fake(
        session: object, *, tenant_id: int, account_id: int, config: object = None
    ) -> dict[str, object]:
        del session, config
        seen.update(tenant_id=tenant_id, account_id=account_id)
        return {
            "status": "succeeded",
            "platform_sync": {
                "administrator": {"provider": "morgan-lloyd", "status": "synced"},
                "custodian": {"status": "pending_ops"},
            },
        }

    monkeypatch.setattr("app.actions.setup.settle_account_setup", _fake)
    h = get_handler("setup_account")
    assert h is not None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=None)
    event = await h.handle(
        _ctx(session=session, subject_type="account", subject_id=42, execution_id=7),
        {},
    )
    assert seen == {"tenant_id": 1, "account_id": 42}
    assert event.detail["platform_sync"]["administrator"]["status"] == "synced"


@pytest.mark.asyncio
async def test_setup_account_names_an_approval_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    """A held plan is not another hand-off. The reason is what the journey reads."""

    async def _fake(
        session: object, *, tenant_id: int, account_id: int, config: object = None
    ) -> dict[str, object]:
        del session, tenant_id, account_id, config
        return {
            "status": "waiting",
            "run_uid": "GcHGcYySHSny",
            "run_status": "awaiting_approval",
            "platform_sync": {},
        }

    monkeypatch.setattr("app.actions.setup.settle_account_setup", _fake)
    h = get_handler("setup_account")
    assert h is not None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=None)
    event = await h.handle(
        _ctx(session=session, subject_type="account", subject_id=42, execution_id=7),
        {},
    )
    assert event.signal is StepSignal.WAITING
    assert event.reason == "awaiting_approval"


@pytest.mark.asyncio
async def test_a_settled_plan_releases_every_setup_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both accounts on one application record the same run. Closing one left the other
    offering the hand-off."""
    from app.flows.orchestrator import on_action_run_settled

    rows = [
        {"case_id": 1, "step_key": "setup", "participant_subject_id": None, "tenant_id": 3},
        {"case_id": 2, "step_key": "setup", "participant_subject_id": None, "tenant_id": 3},
    ]

    class _All:
        def mappings(self) -> "_All":
            return self

        def all(self) -> list[dict[str, object]]:
            return rows

    session = AsyncMock()
    session.execute = AsyncMock(return_value=_All())
    seen: list[tuple[int, object]] = []

    async def _apply(session: object, tenant_id: int, event: object) -> None:
        del session
        seen.append((tenant_id, getattr(event, "case_id", None)))

    monkeypatch.setattr("app.flows.orchestrator.apply_step_event", _apply)
    await on_action_run_settled(session, "GcHGcYySHSny")
    assert seen == [(3, 1), (3, 2)]


@pytest.mark.asyncio
async def test_fill_form_preflight_flags_bad_mapping() -> None:
    h = get_handler("fill_form")
    assert h is not None
    issues = await h.preflight(
        cast(AsyncSession, None),
        1,
        {"form_uid": "f1", "mapping": [{"source": "x", "target": "y", "transforms": ["nope"]}]},
    )
    assert any("mapping" in i.lower() for i in issues)


# ---- request-backed steps with no actor ---------------------------------------------------------
# An account/application-scoped fill_form / accept_terms / upload_document has no contact to bind a
# request to. These used to return None and leave the step `pending` forever with no artifact and no
# actor; they now BLOCK with a reason so the journey shows why. Publish preflight refuses the shape
# outright, so this only fires on an already-published (or seeded) definition.
@pytest.mark.parametrize("step_type", ["fill_form", "accept_terms", "upload_document"])
@pytest.mark.asyncio
async def test_request_backed_step_without_a_request_blocks_loudly(step_type: str) -> None:
    h = get_handler(step_type)
    assert h is not None
    event = await h.instantiate(_ctx(config={"form_uid": "f1", "document_uid": "d1"}))
    assert event is not None, "must not silently return None — the step would never complete"
    assert event.signal == StepSignal.BLOCKED
    assert event.reason == "no_actor"


def test_no_handler_reads_a_config_key_the_validator_rejects() -> None:
    """The other drift direction: a key the handler READS but nobody DECLARES.

    ``config_keys() <= STEP_TYPES`` only compares two declarations, so it cannot see a key that is
    read from ``ctx.config`` and declared in neither — which is exactly how
    ``upload_document``'s and ``generate_document``'s ``title`` shipped as dead branches (the
    validator rejected the config at save, so the ``or`` fallback was the only reachable path).
    Parses the handler module instead of trusting the declarations.
    """
    import ast
    import inspect
    from pathlib import Path

    from app.flows.definitions import STEP_TYPES

    source = Path(inspect.getfile(step_handlers)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    reads_by_class: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        keys: set[str] = set()
        for sub in ast.walk(node):
            # `<anything>.config.get("key")` — ctx.config.get(...) and the bare `config` parameter
            # of preflight() alike.
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "get"
                and sub.args
                and isinstance(sub.args[0], ast.Constant)
                and isinstance(sub.args[0].value, str)
            ):
                target = sub.func.value
                name = (
                    target.attr
                    if isinstance(target, ast.Attribute)
                    else target.id
                    if isinstance(target, ast.Name)
                    else None
                )
                if name == "config":
                    keys.add(sub.args[0].value)
        reads_by_class[node.name] = keys

    for stype, spec in STEP_TYPES.items():
        handler = get_handler(stype)
        assert handler is not None
        read = reads_by_class.get(type(handler).__name__, set())
        undeclared = read - spec.config_keys
        assert not undeclared, (
            f"'{stype}' reads config keys the validator rejects, so those branches are dead: "
            f"{sorted(undeclared)} — declare them in STEP_TYPES (and the handler's _config_keys)"
        )


def test_handler_config_keys_match_the_definition_allowlist() -> None:
    """A key a handler declares but the validator rejects can never be saved (registry drift): the
    `task` handler used to advertise title/assignee that `STEP_TYPES["task"]` refused."""
    from app.flows.definitions import STEP_TYPES

    for stype, spec in STEP_TYPES.items():
        handler = get_handler(stype)
        assert handler is not None, f"no handler registered for '{stype}'"
        assert handler.config_keys() <= spec.config_keys, (
            f"'{stype}' handler declares config keys the validator rejects: "
            f"{sorted(handler.config_keys() - spec.config_keys)}"
        )
