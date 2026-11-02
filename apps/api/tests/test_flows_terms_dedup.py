"""One catalogue acceptance settles account rows created by the pre-dedup planner."""

from unittest.mock import AsyncMock

import pytest

from app.flows import orchestrator
from app.flows.domain_events import StepEvent, StepSignal


class _Rows:
    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict[str, int]]:
        return [{"case_id": 12, "request_item_id": 22, "request_id": 32}]


@pytest.mark.asyncio
async def test_catalogue_acceptance_settles_equivalent_in_flight_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[_Rows(), None, None])
    write = AsyncMock()
    append = AsyncMock()
    emit = AsyncMock()
    monkeypatch.setattr(orchestrator, "_write_step_status", write)
    monkeypatch.setattr(orchestrator, "_append_event", append)
    monkeypatch.setattr(orchestrator, "_emit_task_requests", emit)

    await orchestrator._complete_equivalent_catalogue_terms(
        session,
        tenant_id=4,
        execution_id=8,
        source=StepEvent(
            signal=StepSignal.COMPLETED,
            case_id=11,
            step_key="terms_general",
            participant_subject_id=7,
            detail={"catalogue_term_uid": "term_general"},
        ),
    )

    duplicate = write.await_args.args[3]
    assert duplicate.case_id == 12
    assert duplicate.reason == "already_accepted"
    item_update = str(session.execute.await_args_list[1].args[0])
    assert "request_items SET status = 'fulfilled'" in item_update
    append.assert_awaited_once()
    emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_catalogue_terms_do_not_settle_other_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    write = AsyncMock()
    monkeypatch.setattr(orchestrator, "_write_step_status", write)

    await orchestrator._complete_equivalent_catalogue_terms(
        session,
        tenant_id=4,
        execution_id=8,
        source=StepEvent(
            signal=StepSignal.COMPLETED,
            case_id=11,
            step_key="account_specific_terms",
            participant_subject_id=7,
        ),
    )

    session.execute.assert_not_awaited()
    write.assert_not_awaited()
