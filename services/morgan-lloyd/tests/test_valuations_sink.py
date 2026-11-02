"""The daily valuation sink: ledger rows per scheme/day, dry-run gating.

The fake deliberately exposes `push_policy_valuations` and NOT the old
`push_scheme_valuation`, so a sink that reverted to the placeholder action would fail here
with an AttributeError rather than passing against a mock that answers anything.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pyarrow as pa

_spec = importlib.util.spec_from_file_location(
    "sink_valuations", Path(__file__).resolve().parents[1] / "models" / "sink_valuations.py"
)
assert _spec and _spec.loader
sink = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sink)


class FakeHandle:
    def __init__(self, table: pa.Table) -> None:
        self._table = table

    def table(self) -> pa.Table:
        return self._table


class FakeApi:
    def __init__(self, message: str = "OK", raises: Exception | None = None) -> None:
        self.pushed: list[tuple[str, float, str]] = []
        #: One entry per CALL, so a test can assert on how many requests a book of N schemes cost.
        self.calls: list[list[str]] = []
        self._message = message
        self._raises = raises

    async def __aenter__(self) -> FakeApi:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def push_policy_valuations(self, provider: str, valuations: list[Any]) -> dict[str, Any]:
        self.calls.append([v.policy_reference for v in valuations])
        if self._raises is not None:
            raise self._raises
        for valuation in valuations:
            self.pushed.append(
                (valuation.policy_reference, valuation.policy_value, valuation.valuation_date)
            )
        return {"responseKey": 0, "message": self._message}


async def test_ledger_rows_per_scheme_skipped_while_unwired(
    monkeypatch: Any,
) -> None:
    api = FakeApi()
    monkeypatch.setattr(sink, "MorganLloydClient", lambda: api)
    schemes = FakeHandle(
        pa.table({"scheme_reference": ["MLSIPP/1", "MLSIPP/2"], "ml_tenant_id": ["t1", "t1"]})
    )
    table = await sink.sink_daily_valuations(schemes)
    rows = table.to_pylist()
    assert [r["scheme_reference"] for r in rows] == ["MLSIPP/1", "MLSIPP/2"]
    assert all(r["status"] == "skipped" and r["value"] is None for r in rows)
    assert table.column("value").type == pa.float64()  # typed despite all-NULL
    assert api.pushed == []  # nothing sent: no valuation source and push disabled


def _valuation(policy: str, value: float, day: str) -> Any:
    from morgan_lloyd.endpoints.valuations import FundSplit, PolicyValuation

    return PolicyValuation.from_splits(
        policy,
        day,
        [
            FundSplit(
                name="Fund A",
                valuation_holding=100.0,
                price=value / 100.0,
                market_value=value,
                price_date=day,
            )
        ],
    )


async def test_push_path_records_outcomes(monkeypatch: Any) -> None:
    api = FakeApi()
    monkeypatch.setattr(sink, "MorganLloydClient", lambda: api)
    monkeypatch.setenv("ML_VALUATIONS_PUSH_ENABLED", "true")
    monkeypatch.setenv("MORGAN_LLOYD_INVESTMENT_PROVIDER_ID", "firm-guid")
    monkeypatch.setattr(
        sink, "_resolve_valuation", lambda scheme, today: _valuation("POL-1", 125_000.5, today)
    )
    schemes = FakeHandle(pa.table({"scheme_reference": ["MLSIPP/1"], "ml_tenant_id": ["t1"]}))
    table = await sink.sink_daily_valuations(schemes)
    [row] = table.to_pylist()
    assert row["status"] == "pushed"
    assert row["value"] == 125_000.5
    # The POLICY reference is what was sent, not the scheme reference — the distinction the
    # placeholder action hid.
    assert row["policy_reference"] == "POL-1"
    assert api.pushed and api.pushed[0][0] == "POL-1"


async def test_an_unset_provider_guid_is_named_rather_than_attempted(monkeypatch: Any) -> None:
    """Without it the vendor refuses in a way that reads as a scheme problem, so the ledger
    has to say which value is missing."""
    api = FakeApi()
    monkeypatch.setattr(sink, "MorganLloydClient", lambda: api)
    monkeypatch.setenv("ML_VALUATIONS_PUSH_ENABLED", "true")
    monkeypatch.delenv("MORGAN_LLOYD_INVESTMENT_PROVIDER_ID", raising=False)
    monkeypatch.delenv("MORGAN_LLOYD_ADVISER_FIRM_ID", raising=False)
    monkeypatch.setattr(
        sink, "_resolve_valuation", lambda scheme, today: _valuation("POL-1", 1.0, today)
    )
    schemes = FakeHandle(pa.table({"scheme_reference": ["MLSIPP/1"], "ml_tenant_id": ["t1"]}))
    [row] = (await sink.sink_daily_valuations(schemes)).to_pylist()
    assert row["status"] == "skipped"
    assert "PROVIDER_ID" in row["detail"]
    assert api.pushed == []


# ---- ONE CALL FOR THE BATCH ---------------------------------------------------------------------
#
# `cam_createpolicyvaluation`'s whole contract is a batch: its parameter is `{"Policies": [...]}`
# and it reports failures per policy. The loop sent one request per scheme carrying a
# single-element list, which is ~830 full Dataverse round trips a night on the dev book's pension
# accounts.


def _book(size: int) -> FakeHandle:
    return FakeHandle(
        pa.table(
            {
                "scheme_reference": [f"MLSIPP/{n}" for n in range(size)],
                "ml_tenant_id": ["t1"] * size,
            }
        )
    )


def _pushing(monkeypatch: Any, api: FakeApi) -> None:
    monkeypatch.setattr(sink, "MorganLloydClient", lambda: api)
    monkeypatch.setenv("ML_VALUATIONS_PUSH_ENABLED", "true")
    monkeypatch.setenv("MORGAN_LLOYD_INVESTMENT_PROVIDER_ID", "firm-guid")
    monkeypatch.setattr(
        sink,
        "_resolve_valuation",
        lambda scheme, today: _valuation(
            f"POL-{scheme['scheme_reference'].split('/')[-1]}", 1.0, today
        ),
    )


async def test_a_book_of_schemes_costs_one_call_per_chunk(monkeypatch: Any) -> None:
    api = FakeApi()
    _pushing(monkeypatch, api)

    rows = (await sink.sink_daily_valuations(_book(250))).to_pylist()

    assert [len(call) for call in api.calls] == [sink._CHUNK, sink._CHUNK, 50]
    assert len(api.pushed) == 250
    assert {row["status"] for row in rows} == {"pushed"}


async def test_the_policies_the_vendor_names_as_failed_are_the_ones_marked_failed(
    monkeypatch: Any,
) -> None:
    """One message answers the whole batch and names the policies it refused — `Failed Policies:
    <ref> (missing policy)`. The delimiter between several is undocumented and unobserved, so the
    reading is deliberately a substring one: a reference the message NAMES is failed, and one it
    does not mention is pushed."""
    api = FakeApi(message="Failed Policies: POL-1 (missing policy)")
    _pushing(monkeypatch, api)

    rows = {
        r["policy_reference"]: r for r in (await sink.sink_daily_valuations(_book(3))).to_pylist()
    }

    assert rows["POL-1"]["status"] == "failed"
    assert rows["POL-0"]["status"] == "pushed"
    assert rows["POL-2"]["status"] == "pushed"
    # The vendor's own words, on every row of the chunk, because the attribution reads prose.
    assert all("Failed Policies" in row["detail"] for row in rows.values())


async def test_a_message_reporting_no_failure_marks_every_policy_pushed(monkeypatch: Any) -> None:
    api = FakeApi(message="2 policy valuations created")
    _pushing(monkeypatch, api)

    rows = (await sink.sink_daily_valuations(_book(2))).to_pylist()
    assert {row["status"] for row in rows} == {"pushed"}


async def test_a_refused_chunk_fails_its_own_rows_and_not_the_book(monkeypatch: Any) -> None:
    """A vendor error takes the whole chunk with it — the action either parsed the payload or did
    not, so there is no per-policy outcome to read — and `_CHUNK` is what bounds the cost."""
    api = FakeApi(raises=RuntimeError("0x80040224 Object reference not set"))
    _pushing(monkeypatch, api)
    monkeypatch.setattr(sink, "_CHUNK", 2)

    rows = (await sink.sink_daily_valuations(_book(4))).to_pylist()

    assert len(api.calls) == 2, "the second chunk is still attempted"
    assert {row["status"] for row in rows} == {"failed"}
    assert all("Object reference" in row["detail"] for row in rows)


async def test_nothing_to_send_opens_no_client_at_all(monkeypatch: Any) -> None:
    """The dry-run and skipped paths decide before any HTTP does — the client used to be opened
    for a book of schemes that all resolved to `skipped`."""

    def _no_client() -> Any:
        raise AssertionError("must not open a client with nothing to send")

    monkeypatch.setattr(sink, "MorganLloydClient", _no_client)
    rows = (await sink.sink_daily_valuations(_book(2))).to_pylist()
    assert {row["status"] for row in rows} == {"skipped"}
