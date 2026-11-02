"""The generated raw layer stays in step with the declared schema.

``models/raw_reports.py`` is generated from ``sei_sync.schema`` by
``scripts/generate_raw_models.py``. Nothing stops someone editing the module by hand
or changing a declared type without regenerating, and the failure is quiet: the model
still runs, it just converts the wrong columns. This is the gate that makes that loud.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_raw_models as generator  # noqa: E402

from sei_sync.reports import KNOWN_REPORTS, table_name  # noqa: E402

MODULE = Path(__file__).resolve().parent.parent / "models" / "raw_reports.py"


def test_generated_module_is_up_to_date() -> None:
    """Fails when the checked-in module differs from what the generator would write —
    the same check `scripts/generate_raw_models.py --check` runs."""
    assert MODULE.read_text(encoding="utf-8") == generator.render(), (
        "models/raw_reports.py is stale — run `uv run python scripts/generate_raw_models.py`"
    )


def test_every_known_report_has_a_model() -> None:
    source = MODULE.read_text(encoding="utf-8")
    for report in KNOWN_REPORTS:
        assert f'name="raw.{table_name(report)}"' in source, report


def test_generated_lines_fit_the_line_length_limit() -> None:
    """The generated SQL sits inside a Python string, so `ruff` still counts its lines.
    Wrapping is the generator's job — this keeps a new long column name from breaking
    the lint gate rather than the tests."""
    too_long = [
        (n, line)
        for n, line in enumerate(MODULE.read_text(encoding="utf-8").splitlines(), 1)
        if len(line) > 100
    ]
    assert not too_long, f"{len(too_long)} generated line(s) over 100 chars: {too_long[:3]}"


def test_generated_module_imports_and_registers_every_model() -> None:
    """Guards the shape of the generated code, not just its text: a template change that
    produced unparseable Python or a duplicate model name would fail here."""
    decorators = pytest.importorskip("interlace.dsl.decorators")
    registry = decorators.REGISTRY
    registry.clear()
    try:
        spec = importlib.util.spec_from_file_location("sei_raw_reports_under_test", MODULE)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        registered = {name for name in registry.models if name.startswith("raw.")}
        assert registered == {f"raw.{table_name(r)}" for r in KNOWN_REPORTS}
    finally:
        registry.clear()
        sys.modules.pop("sei_raw_reports_under_test", None)


def test_declared_types_reach_the_generated_sql() -> None:
    """A column declared DATE must actually be parsed in the model that reads it."""
    source = MODULE.read_text(encoding="utf-8")
    assert "try_strptime(\"TRANSACTION_DATE\", '%Y%m%d')::DATE" in source
    assert "CASE \"DORMANT_STATUS_FLAG\" WHEN 'Y' THEN true" in source
    # ...and an identifier must not be converted at all
    assert '"ACCOUNT_NUMBER" AS "ACCOUNT_NUMBER"' not in source
