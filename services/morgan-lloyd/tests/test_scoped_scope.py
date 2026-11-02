"""How the scheme- and adviser-scoped models decide WHAT to ask for, and what counts as empty.

Both questions have been answered wrongly in production, and both failures looked identical from
the outside: a table with no rows, which reads as "this firm has no data" rather than as a bug.

  * the scheme scope read `cam_schemerefs`, which is the reference SERIES, not the schemes. Every
    per-ref call was refused, four models had never produced a row, and the overlap between the
    two populations was ZERO;
  * the adviser scope read an env var nobody set;
  * and several of these actions report "nothing here" by RAISING, so an empty scheme was being
    counted as a failed one.

These are unit tests over the decision, not over the vendor: the live behaviour was verified by
calling the org directly (Aug 2026) and is recorded in the module docstring.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# models/ is not a package on the path (interlace imports it by discovery), so the module is
# loaded by location -- the same approach test_valuations_sink.py takes.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from morgan_lloyd import MorganLloydActionError  # noqa: E402

from models.raw_scoped import (  # noqa: E402
    _SCHEME_REF_FIELD,
    _SCHEME_SET,
    _is_empty_result,
    _scheme_refs,
)


class _FakeApi:
    """Records the collection it was asked for, and answers with whatever it was given."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.asked: list[str] = []

    async def get_collection(self, path: str, params: Any = None) -> list[dict[str, Any]]:
        self.asked.append(path)
        return self.records


class TestSchemeScope:
    @pytest.mark.asyncio
    async def test_it_enumerates_schemes_not_the_reference_series(self) -> None:
        """THE BUG. `cam_schemerefs` holds `QSIPP/00652`-style series identifiers; the actual
        references are `cam_schemes.cam_name` (`MLSIPP/01417`). Live, every series id was refused
        by cam_getscheme and crashed cam_getaccounttransactions with a 400."""
        api = _FakeApi([{"cam_name": "MLSIPP/01417"}])
        await _scheme_refs(api)  # type: ignore[arg-type]
        assert api.asked, "no collection was read"
        assert _SCHEME_SET == "cam_schemes"
        assert api.asked[0].startswith("cam_schemes")
        assert "cam_schemerefs" not in api.asked[0]

    @pytest.mark.asyncio
    async def test_refs_are_deduplicated_and_sorted(self) -> None:
        api = _FakeApi(
            [
                {"cam_name": "MLSIPP/02038"},
                {"cam_name": "MLSIPP/01417"},
                {"cam_name": "MLSIPP/01417"},
                {"cam_name": "  "},
                {"cam_name": None},
            ]
        )
        assert await _scheme_refs(api) == ["MLSIPP/01417", "MLSIPP/02038"]  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_the_env_override_narrows_without_calling_the_crm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ML_SYNC_SCHEME_REFS is a backfill override, not the thing that makes these work."""
        monkeypatch.setenv("ML_SYNC_SCHEME_REFS", "MLSIPP/00001,MLSIPP/00002")
        api = _FakeApi([{"cam_name": "MLSIPP/99999"}])
        assert await _scheme_refs(api) == ["MLSIPP/00001", "MLSIPP/00002"]  # type: ignore[arg-type]
        assert api.asked == [], "the override must not still hit the CRM"

    def test_the_reference_field_is_the_scheme_name(self) -> None:
        assert _SCHEME_REF_FIELD == "cam_name"


class TestEmptyIsNotAFailure:
    """Several actions report "nothing here" by raising a failed envelope.

    The distinction matters in both directions: counting an empty scheme as a failure logs a
    warning per scheme per run and buries the real failures, while counting a real failure as
    empty hides a reference we cannot resolve.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "cam_getaccounttransactions: No Financial Transactions were found",
            "cam_getadvisercontacts: No Adviser Found for Given Email Address",
            "cam_getreceivedcontributions: No Contributions Found",
            "cam_getscheme: No records exist for this scheme",
        ],
    )
    def test_empty_answers_are_recognised(self, message: str) -> None:
        assert _is_empty_result(MorganLloydActionError(message))

    @pytest.mark.parametrize(
        "message",
        [
            # The one that must NOT be swallowed: a reference we cannot resolve is a problem,
            # and it is the exact shape the scheme-scope bug produced 7 times a run.
            "cam_getscheme: Scheme QSIPP/00652 not found",
            "cam_getscheme: Scheme DSIPP/10372 not found",
            "POST cam_getaccounttransactions failed: 400 Object reference not set",
            "cam_createcontact: gendercode is required",
        ],
    )
    def test_real_failures_are_not_swallowed(self, message: str) -> None:
        assert not _is_empty_result(MorganLloydActionError(message))
