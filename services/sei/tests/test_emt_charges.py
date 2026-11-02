"""The EMT extract collapses to one current charge per ISIN."""

from __future__ import annotations

import importlib.util
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from sei_sync.emt import collapse, digest, publish, read_csv, write_csv

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "emt_charges.csv"
MODEL = ROOT / "models" / "emt_charges.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("emt_charges", MODEL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


emt_charges = _load()


def _row(
    isin: str,
    *,
    created: str,
    actual: str | None = None,
    estimate: str | None = None,
    actual_on: str | None = None,
    estimate_on: str | None = None,
) -> dict[str, str | None]:
    return {
        "isin": isin,
        "sedol": "SED001",
        "file_created_on": created,
        "ongoing_charge_actual": actual,
        "ongoing_charge_estimate": estimate,
        "ongoing_charge_actual_on": actual_on,
        "ongoing_charge_estimate_on": estimate_on,
    }


def test_the_latest_file_date_is_the_charge() -> None:
    """An older snapshot can carry a later charge-effective date. The publication
    date is what 'current' means — that is the row the reporting UI added last."""
    rows = collapse(
        [
            _row("GB00TEST0001", created="2026-01-30", actual="0.40", actual_on="2026-09-01"),
            _row("GB00TEST0001", created="2026-09-25", actual="0.55", actual_on="2025-02-28"),
        ]
    )
    assert len(rows) == 1
    assert rows[0]["file_created_on"] == date(2026, 9, 25)
    assert rows[0]["ocf"] == Decimal("0.5500000000")
    assert rows[0]["ongoing_charge_actual"] == Decimal("0.5500000000")


def test_a_zero_actual_is_kept() -> None:
    rows = collapse([_row("GB00TEST0001", created="2026-09-25", actual="0", estimate="0.40")])
    assert rows[0]["ocf"] == Decimal("0.0000000000")


def test_an_estimate_is_used_when_the_snapshot_has_no_actual() -> None:
    rows = collapse([_row("GB00TEST0001", created="2026-09-25", estimate="0.40")])
    assert rows[0]["ocf"] == Decimal("0.4000000000")


def test_a_blank_isin_is_dropped_and_each_isin_is_one_row() -> None:
    rows = collapse(
        [
            _row("", created="2026-09-25", actual="0.10"),
            _row("AAA", created="2026-01-30", actual="0.10"),
            _row("AAA", created="2026-09-25", actual="0.20"),
            _row("BBB", created="2026-09-25", actual="0.30"),
        ]
    )
    assert [row["isin"] for row in rows] == ["AAA", "BBB"]
    assert rows[0]["ocf"] == Decimal("0.2000000000")


def test_collapsing_twice_changes_nothing() -> None:
    once = collapse(
        [
            _row("AAA", created="2026-01-30", actual="0.10"),
            _row("AAA", created="2026-09-25", actual="0.20"),
        ]
    )
    assert collapse(once) == once


def test_a_file_that_does_not_match_the_model_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "emt.csv"
    write_csv(path, collapse([_row("AAA", created="2026-09-25", actual="0.20")]))
    with pytest.raises(RuntimeError, match="does not match"):
        publish(path, expected="0" * 64)


def test_an_override_file_collapses_without_a_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "emt.csv"
    write_csv(
        path,
        [
            _row("AAA", created="2026-01-30", actual="0.10"),
            _row("AAA", created="2026-09-25", actual="0.20"),
            _row("BBB", created="2026-09-25", estimate="0.30"),
        ],
    )
    monkeypatch.setenv("SEI_EMT_CHARGES_CSV", str(path))
    table = emt_charges.platform_emt_charges()
    assert isinstance(table, pa.Table)
    got = {row["isin"]: row["ocf"] for row in table.to_pylist()}
    assert got == {"AAA": Decimal("0.2000000000"), "BBB": Decimal("0.3000000000")}


def test_the_shipped_extract_matches_the_model() -> None:
    """The CSV and the digest literal are one artefact. Editing either by hand
    publishes a charge the planner's fingerprint did not include."""
    source = MODEL.read_text(encoding="utf-8")
    found = re.search(r'digest = "([0-9a-f]{64})"', source)
    assert found is not None
    assert digest(CSV) == found.group(1)
    rows = collapse(read_csv(CSV))
    assert len(rows) == len({row["isin"] for row in rows})
    assert rows
    assert all(row["ocf"] is not None for row in rows)
