"""The shared run loop used by both the scheduler and the one-shot runner."""

import pytest

from intelliflo_sync.shared import runner
from intelliflo_sync.shared.base.exceptions import FeatureError


class _Integration:
    calls: list = []
    outcome = None

    def run(self, tenant_id, features):
        type(self).calls.append(list(features))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return {f: {"n": 1} for f in features}


@pytest.fixture
def recorded(monkeypatch):
    out = {"jobs": [], "ok": [], "bad": []}
    _Integration.calls = []
    _Integration.outcome = None
    monkeypatch.setattr(runner, "get_data_date", lambda: "20260102")
    monkeypatch.setattr(runner, "has_successful_run_for_date", lambda i, f, d: f in out.get("done", ()))
    monkeypatch.setattr(
        runner, "record_job_run", lambda i, f, ok, err=None, data=None, data_date=None: out["jobs"].append((f, ok, err))
    )
    monkeypatch.setattr(runner, "notify_success", lambda name, f, d, data: out["ok"].append(f))
    monkeypatch.setattr(runner, "notify_failure", lambda name, f, d, msg: out["bad"].append((f, msg)))
    return out


def test_the_features_run_in_one_call_so_they_share_a_lake_connection(recorded):
    runner.run_features(_Integration, "IO", "1", "t", ["Valuations", "Transactions"])
    assert _Integration.calls == [["Valuations", "Transactions"]]
    assert recorded["ok"] == ["Valuations", "Transactions"]


def test_features_that_already_succeeded_are_not_run_again(recorded):
    recorded["done"] = {"Valuations"}
    runner.run_features(_Integration, "IO", "1", "t", ["Valuations", "Transactions"])
    assert _Integration.calls == [["Transactions"]]


def test_only_the_failed_features_are_recorded_failed(recorded):
    _Integration.outcome = FeatureError(
        "boom", data={"results": {"Valuations": {}, "Transactions": {}}, "failed_features": {"Transactions"}}
    )
    runner.run_features(_Integration, "IO", "1", "t", ["Valuations", "Transactions"])
    assert [(f, ok) for f, ok, _ in recorded["jobs"]] == [("Valuations", True), ("Transactions", False)]
    assert recorded["bad"] == [("Transactions", "boom")]


def test_a_timeout_fails_every_feature_and_is_reported_to_the_caller(recorded):
    _Integration.outcome = TimeoutError("slow")
    assert runner.run_features(_Integration, "IO", "1", "t", ["Valuations"]) is True
    assert recorded["bad"] == [("Valuations", "Timed out: slow")]


def test_any_other_error_fails_every_feature_without_raising(recorded):
    _Integration.outcome = RuntimeError("x")
    assert runner.run_features(_Integration, "IO", "1", "t", ["Valuations", "Transactions"]) is False
    assert [ok for _, ok, _ in recorded["jobs"]] == [False, False]
