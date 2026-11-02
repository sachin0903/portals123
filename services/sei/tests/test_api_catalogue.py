"""Model names and ISA/JISA allowances from the SWP reads that actually answer.

The assignment read is one portfolio's model, not a catalogue, and it has no target
return, charge or holdings. Account details has the ISA and JISA contribution block
and nothing for a SIPP. These pin the selection and the columns that are kept.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from sei_sync.api import AccountRef, SyncStats
from sei_sync.pull import fetch_account_allowances, fetch_model_assignments

_MODELS = Path(__file__).resolve().parents[1] / "models"
NOW = datetime(2026, 10, 5, 7, 15, tzinfo=UTC)


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _MODELS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_models = _load("api_models")
api_allowances = _load("api_allowances")


class _Handle:
    def __init__(self, table: pa.Table) -> None:
        self._table = table

    def table(self) -> pa.Table:
        return self._table


def _accounts() -> _Handle:
    return _Handle(
        pa.table(
            {
                "FIRM_ID": ["3106", "3106", "3116", "3116", "3102"],
                "MODEL_ID": ["87741", "87741", "90373", "90373", None],
                "ACCOUNT_NUMBER": ["MSP000011", "MSP000012", "CRFP00030", "CRFP00031", "X"],
                "PORTFOLIO_ID": ["1", "2", "59023839", "59023840", "9"],
                "PORTFOLIO_TYPE": ["190", "189", "189", "190", "189"],
                "ACCOUNT_TYPE": ["172", "157", "157", "173", "172"],
            }
        )
    )


class TestModelTargets:
    def test_one_capital_portfolio_per_model_in_the_requested_firms(self) -> None:
        """87741 has an Income row and a Capital row. The read refuses Income, so the
        Capital account is the one requested. 3102 is not in the firm list."""
        targets = api_models.select_targets(_accounts(), {"3106", "3116"})
        assert targets == [
            ("3106", "87741", "MSP000012", "2"),
            ("3116", "90373", "CRFP00030", "59023839"),
        ]

    def test_no_firms_requests_nobody(self) -> None:
        assert api_models.select_targets(_accounts(), set()) == []


class TestAllowanceAccounts:
    def test_only_isa_and_jisa_accounts_are_requested(self) -> None:
        """172 and 173. A SIPP is 157 and is not requested: the contribution block on
        that account is null, and there is no other read that states a pension allowance."""
        accounts = api_allowances.select_accounts(_accounts(), {"3106", "3116", "3102"})
        assert [(a.firm_id, a.account_number) for a in accounts] == [
            ("3102", "X"),
            ("3106", "MSP000011"),
            ("3116", "CRFP00031"),
        ]


class TestConform:
    def test_the_assignment_keeps_the_name_and_drops_the_holder(self) -> None:
        table = api_models.conform(
            [
                {
                    "_firm_id": "3116",
                    "_model_id": "90373",
                    "modelName": "CR Implementation Model Test",
                    "modelType": 4,
                    "modelTypeDesc": "Implementation Model",
                    "toleranceMultiplier": 1,
                    "entityName": "a person",
                }
            ],
            as_of=NOW.date(),
            fetched_at=NOW,
        )
        [row] = table.to_pylist()
        assert row["model_name"] == "CR Implementation Model Test"
        assert row["model_type"] == "Implementation Model"
        assert row["model_type_code"] == "4"
        assert "entityName" not in row
        assert int(row["tolerance_multiplier"]) == 1

    def test_the_jisa_limit_uses_seis_own_spelling(self) -> None:
        """The spec spells that one field `jisaClientLevelcontributionlimit`."""
        table = api_allowances.conform(
            [
                {
                    "_firm_id": "3106",
                    "_account_number": "MSP000594",
                    "taxYear": "2026-2027",
                    "isaClientLevelYearToDateContribution": "0",
                    "isaClientLevelRemainingContribution": "0",
                    "jisaClientLevelcontributionlimit": "9000",
                    "jisaClientLevelYearToDateContribution": "0",
                    "jisaClientLevelRemainingContribution": "9000",
                    "clientName": "a person",
                }
            ],
            as_of=NOW.date(),
            fetched_at=NOW,
        )
        [row] = table.to_pylist()
        assert row["tax_year"] == "2026-2027"
        assert int(row["jisa_limit"]) == 9000
        assert int(row["jisa_remaining"]) == 9000
        assert "clientName" not in row

    def test_an_empty_conform_is_typed(self) -> None:
        table = api_models.conform([], as_of=NOW.date(), fetched_at=NOW)
        assert table.num_rows == 0
        assert table.schema.field("model_id").type == pa.string()


class _Settings:
    api_concurrency = 2


class _Api:
    def __init__(self, settings: object) -> None:
        self.settings = settings

    async def __aenter__(self) -> _Api:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def model_assignment(
        self, firm_id: str, account_number: str, portfolio_id: str
    ) -> dict[str, Any]:
        if model_id := {"MSP000012": "87741"}.get(account_number):
            return {
                "modelId": int(model_id),
                "modelName": "Marlborough Fund (M) Risk profile 6",
                "modelTypeDesc": "Implementation Model",
                "entityName": "a person",
                "status": "SUCCESS",
            }
        return {"modelId": 1, "modelName": "someone else's model", "entityName": "a person"}

    async def account_details(self, firm_id: str, account_number: str) -> dict[str, Any]:
        if account_number == "MISSING":
            raise RuntimeError("HTTP 400")
        return {
            "accountOwners": [{"clientName": "a person"}],
            "isaJisaContributionDetails": {
                "taxYear": "2026-2027",
                "isaClientLevelYearToDateContribution": "0",
                "isaClientLevelRemainingContribution": "20000",
            },
        }


class TestTheReads:
    async def test_a_portfolio_answering_a_different_model_is_not_stored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sei_sync.pull.SeiApi", _Api)
        rows, stats = await fetch_model_assignments(
            _Settings(),  # type: ignore[arg-type]
            [("3106", "87741", "MSP000012", "2"), ("3106", "99999", "OTHER", "1")],
        )
        assert len(rows) == 1
        assert rows[0]["modelName"] == "Marlborough Fund (M) Risk profile 6"
        assert "entityName" not in rows[0]
        assert "3106/99999" in stats.failures

    async def test_one_account_failing_does_not_drop_the_other(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sei_sync.pull.SeiApi", _Api)
        rows, stats = await fetch_account_allowances(
            _Settings(),  # type: ignore[arg-type]
            [
                AccountRef("3106", "MSP000011"),
                AccountRef("3106", "MISSING"),
            ],
        )
        assert [row["_account_number"] for row in rows] == ["MSP000011"]
        assert rows[0]["taxYear"] == "2026-2027"
        assert "accountOwners" not in rows[0]
        assert stats.failures["3106/MISSING"].startswith("HTTP 400")
        assert isinstance(stats, SyncStats)


class _NoApi:
    api_base_url = ""


class TestDisabled:
    async def test_no_api_base_url_returns_an_empty_model_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(api_models, "SeiSettings", _NoApi)
        table = await api_models.raw_api_models(_accounts())
        assert table.num_rows == 0
