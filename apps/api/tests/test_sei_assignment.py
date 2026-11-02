"""SEI model assignment picks the variant from the published model type."""

import pytest

from app.actions.attempt import OperationError
from app.integrations.sei.assignment import PATHS, assignment_request


def test_an_investment_model_uses_the_investment_path() -> None:
    path, body = assignment_request(
        firm_id="3108",
        account_number="AIO00216",
        portfolio_id="59103919",
        model_id="83011",
        model_type="8",
        tolerance="1",
        schedule_id="51",
    )
    assert path == PATHS["8"]
    assert body["firmId"] == 3108
    assert body["accountNumber"] == "AIO00216"
    assert body["portfolioId"] == 59103919
    assert body["modelId"] == 83011
    assert body["toleranceMultiplier"] == 1
    assert body["eventProcessingId"] == 3
    assert body["apsId"] == 51
    assert "sleeveConfigurationType" not in body


def test_an_implementation_model_names_the_sleeve() -> None:
    path, body = assignment_request(
        firm_id="3108",
        account_number="AIO00216",
        portfolio_id="59103919",
        model_id="83012",
        model_type="4",
        tolerance=0.2,
    )
    assert path == PATHS["4"]
    assert body["sleeveConfigurationType"] == 1
    assert "apsId" not in body


def test_an_allocation_model_does_not_send_a_schedule() -> None:
    path, body = assignment_request(
        firm_id="3108",
        account_number="AIO00216",
        portfolio_id="59103919",
        model_id="83013",
        model_type="3",
        tolerance=1,
        schedule_id="51",
    )
    assert path == PATHS["3"]
    assert "apsId" not in body
    assert "sleeveConfigurationType" not in body


def test_a_model_without_a_tolerance_or_a_known_type_is_not_sent() -> None:
    with pytest.raises(OperationError, match="tolerance") as missing:
        assignment_request(
            firm_id="3108",
            account_number="AIO00216",
            portfolio_id="59103919",
            model_id="83011",
            model_type="8",
            tolerance=None,
        )
    assert missing.value.permanent is True
    with pytest.raises(OperationError, match="type 5") as unknown:
        assignment_request(
            firm_id="3108",
            account_number="AIO00216",
            portfolio_id="59103919",
            model_id="83011",
            model_type="5",
            tolerance=1,
        )
    assert unknown.value.permanent is True


@pytest.mark.asyncio
async def test_a_model_waits_again_when_the_account_is_not_open_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WIP Submitted is not a permanent failure. The next drain polls again."""
    from app.integrations.sei import actions

    class _Result:
        def __init__(self, row: object) -> None:
            self._row = row

        def mappings(self) -> "_Result":
            return self

        def one_or_none(self) -> object:
            return self._row

        def scalar_one_or_none(self) -> object:
            return self._row

    class _Session:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, sql: object, params: object = None) -> _Result:
            del sql, params
            self.calls += 1
            if self.calls == 1:
                return _Result(
                    {
                        "model_id": 1,
                        "sei_model": "83011",
                        "sei_account": "AIO00216",
                        "sei_portfolio": "59103919",
                    }
                )
            return _Result({"firm_id": "3108"})

    class _Api:
        async def __aenter__(self) -> "_Api":
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    async def _wait(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OperationError("The account is still inside enrolment.", permanent=False)

    monkeypatch.setattr(
        "app.integrations.sei.cash_receipts.configured",
        lambda: True,
    )
    monkeypatch.setattr("app.integrations.sei.cash_receipts._settings", object)
    monkeypatch.setattr(
        "app.integrations.sei.assignment.published_model",
        lambda *_args: {
            "model_type_code": "8",
            "tolerance_multiplier": "1",
            "schedule_id": "51",
        },
    )
    monkeypatch.setattr(actions, "wait_until_accounts_open", _wait)
    monkeypatch.setattr("sei.SeiClient", lambda _settings: _Api())
    with pytest.raises(OperationError, match="inside enrolment") as waiting:
        await actions.sei_attach_model(
            _Session(),  # type: ignore[arg-type]
            payload={"tenant_id": 1, "account_id": 2},
            subjects=[],
        )
    assert waiting.value.permanent is False
