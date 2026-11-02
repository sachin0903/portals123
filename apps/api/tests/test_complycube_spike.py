"""The sandbox spike script's guard and its report helper. The script itself needs a real key."""

from __future__ import annotations

import pytest

from scripts.complycube_spike import key_paths, refuse_live_key


@pytest.mark.parametrize("key", ["", "   ", "live_abc123", "sk_abc", "abc_test_"])
def test_anything_but_a_sandbox_key_is_refused(key: str) -> None:
    assert refuse_live_key(key) is not None


def test_a_sandbox_key_is_accepted() -> None:
    assert refuse_live_key("test_abc123") is None


def test_a_live_key_runs_only_when_chosen() -> None:
    assert refuse_live_key("live_abc123") is not None
    assert refuse_live_key("live_abc123", allow_live=True) is None
    assert refuse_live_key("sk_abc", allow_live=True) is not None


def test_key_paths_lists_every_nested_key() -> None:
    result = {
        "outcome": "clear",
        "breakdown": {"faceAnalysis": {"liveness": "clear"}, "matches": [{"id": "m1"}]},
    }
    assert key_paths(result) == [
        "breakdown",
        "breakdown.faceAnalysis",
        "breakdown.faceAnalysis.liveness",
        "breakdown.matches",
        "breakdown.matches[].id",
        "outcome",
    ]
