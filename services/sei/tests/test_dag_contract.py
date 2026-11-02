"""The DAG's shape, asserted without a warehouse.

interlace validates a Python model's *parameters* when it runs it, not when it plans it
(``runtime/python_model.py``: a parameter that is neither reserved nor a declared
dependency raises ``DefinitionError`` at execution). So a bad signature on `raw.files`
would pass every gate and then fail at 05:00 in a scheduled run. These tests apply the
same rules the runtime does, against the real compiled project, so the failure lands here
instead.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

# `import interlace.runtime.python_model` on its own raises ImportError on interlaced
# 1.0.2: runtime/__init__ -> python_model -> plan/__init__ -> apply -> python_model, which
# is still partially initialised. Importing interlace.plan first enters the cycle at a
# point that resolves — a library bug, not ours, and this import is the workaround.
import interlace.plan  # noqa: F401
import pytest
from interlace.project import Project
from interlace.runtime.python_model import RESERVED_PARAMS, _param_spellings

from sei_sync.reports import KNOWN_REPORTS, table_name

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def project():  # noqa: ANN201 — interlace's CompiledProject
    """Discover and compile the real project. No engine, no warehouse, no network."""
    return Project.load(ROOT).compile()


def test_every_model_parameter_is_reserved_or_a_declared_dependency(project) -> None:  # noqa: ANN001
    """The check interlace only makes at run time, made at test time."""
    for model in project.models.values():
        if model.fn is None:
            continue  # a SQL model
        spellings = _param_spellings(model.dependencies)
        for name in inspect.signature(model.fn).parameters:
            assert name in spellings or name in RESERVED_PARAMS, (
                f"{model.name} takes {name!r}, which is neither a reserved parameter "
                f"nor one of its dependencies {model.dependencies}"
            )


def test_transfer_providers_are_extracted_from_reference(project) -> None:  # noqa: ANN001
    """The ceding counterparty list is its own model, not a filter a caller has to know."""
    model = project.models["ref.transfer_providers"]
    assert "ref.reference_current" in model.dependencies
    sql = (ROOT / "models" / "ref" / "transfer_providers.sql").read_text()
    for code_type in ("TRANSFER_AGENT_ID", "CUSTODIAN", "CASH_AGENT", "TAX_WRAP_PROVIDER"):
        assert code_type in sql
    assert "BROKER" not in sql


def test_raw_files_is_upstream_of_every_report(project) -> None:
    """The whole point of the change: a report cannot read a drop that was never
    fetched. If one of the 47 loses the dependency, its 05:30 run reads yesterday."""
    assert "raw.files" in project.models
    for report in KNOWN_REPORTS:
        model = project.models[f"raw.{table_name(report)}"]
        assert "raw.files" in model.dependencies, report


def test_raw_files_declares_a_cursorless_merge_on_the_filename(project) -> None:
    """Keyed on the filename so a retry supersedes its failed row rather than
    accumulating beside it, and cursorless because the drop is the source of truth about
    what exists — not a high-water mark in the warehouse."""
    model = project.models["raw.files"]
    assert model.strategy == "merge"
    assert tuple(model.key) == ("filename",)
    assert model.cursor is None


def test_raw_files_is_scheduled_before_the_reports(project) -> None:
    """A scheduled fire runs the model and its descendants, and does not pull ancestors
    (interlace.scheduler.engine.scheduled_closure, since 2.8.3). The gap is what puts a
    report cron behind the fetch. depends_on orders an ad-hoc run."""
    files = project.models["raw.files"].schedule
    report = project.models["raw.accounts"].schedule
    assert files == {"cron": "0 5 * * 2-6"}
    assert report == {"cron": "30 5 * * 2-6"}
    assert _minutes(files["cron"]) < _minutes(report["cron"])


def _minutes(cron: str) -> int:
    minute, hour = cron.split()[:2]
    return int(hour) * 60 + int(minute)


def test_the_reports_read_the_staged_zone_of_the_core_bucket(project) -> None:
    """The path the pull writes and the path the reports read are one edit apart and in
    different files; this is what keeps them the same path."""
    source = (ROOT / "models" / "raw_reports.py").read_text(encoding="utf-8")
    assert "s3://{bucket}/sei/staged/" in source
    # The env segment is gone for good: the bucket is per environment.
    assert "/staged/sei/{env}/" not in source
    assert "{env}" not in source


def _declared_empty_columns() -> tuple[str, ...]:
    """What the generated models pass as `empty_columns` — one shape for all 47, read from the
    source rather than imported, because it is the SOURCE that interlace fingerprints."""
    source = (Path(__file__).resolve().parent.parent / "models" / "raw_reports.py").read_text()
    found = set(re.findall(r"empty_columns=\(([^)]*)\)", source))
    assert len(found) == 1, f"the models disagree on the empty shape: {found}"
    return tuple(part.strip().strip('"') for part in found.pop().split(",") if part.strip())


class TestAnUndeliveredReportDoesNotFailTheRun:
    """SEI declares 47 reports and delivers 46. `FSLTaxData` has never arrived on the i31 drop, so
    its glob matches nothing and DuckDB raises `IO Error: No files found` — which `raw_read` turns
    into an empty table, because one undelivered report must not fail the other 46.
    """

    def test_the_empty_stub_satisfies_the_models_whole_contract(self, project) -> None:  # noqa: ANN001
        """Its KEY and its CURSOR, because interlace reads both off the previous materialisation
        and neither failure names the real reason.

        Both halves were bugs. `("_source_file", "_row")` is the merge key and the DELETE selects
        it from the new snapshot, so a stub carrying only `_source_file` died on
        `Referenced column "_row" not found`. `dt` is the cursor, asked for as `max(dt)` on every
        run AFTER the first — which is why that half hid: an `apply` with no previous
        materialisation succeeded, and only the re-run broke.

        Read from the MODEL's own call, not from the helper's default: the shape has to sit inside
        `inspect.getsource(fn)` or correcting it cannot rebuild the table it got wrong.
        """
        from sei_sync.raw_read import _EMPTY_TYPES, _empty

        model = project.models["raw.fsl_tax_data"]
        declared = _declared_empty_columns()
        required = set(model.key) | {model.cursor}
        assert required == {"_source_file", "_row", "dt"}, (
            f"the model's key/cursor moved to {sorted(required)}; the stub must move with it"
        )
        assert required <= set(declared), (
            f"the models declare empty_columns={declared} and need {sorted(required)} — the read "
            f"would fail on the missing column, not on the absent file"
        )
        assert set(declared) <= set(_EMPTY_TYPES), "a declared column with no type would raise"
        assert _empty(declared).num_rows == 0

    def test_the_stubs_cursor_is_a_date_like_the_real_tables(self, project) -> None:  # noqa: ANN001
        """`dt` is a DATE everywhere else (a hive partition read as one). A VARCHAR here would
        either compare wrongly against the date the cursor is filtered on, or fail the merge on a
        type mismatch the day the report starts arriving."""
        import pyarrow as pa

        from sei_sync.raw_read import _empty

        assert pa.types.is_date(_empty(_declared_empty_columns()).schema.field("dt").type)

    def test_every_report_model_shares_that_key_and_cursor(self, project) -> None:  # noqa: ANN001
        """The stub is one shape for all 47, so a model that keyed or cursored itself differently
        would get a stub that cannot merge — and only on the day its report stopped arriving."""
        for report in KNOWN_REPORTS:
            model = project.models[f"raw.{table_name(report)}"]
            assert set(model.key) == {"_source_file", "_row"}, report
            assert model.cursor == "dt", report


#: Every conformed relation lives here as a `.sql` file with an interlace frontmatter block.
CONFORMED = ROOT / "models" / "platform"


@pytest.mark.parametrize("path", sorted(CONFORMED.glob("*.sql")), ids=lambda path: path.stem)
def test_a_conformed_model_declares_every_platform_relation_it_joins(path: Path) -> None:
    """interlace resolves `depends_on` at PLAN time and the join at APPLY time.

    So an undeclared dependency PLANS CLEAN and then reads a relation the run has not built yet —
    which is a Binder Error that aborts the tenant's whole projection and lands in the ledger as
    "unavailable", i.e. indistinguishable from "this firm has no data with this custodian". It is
    the same shape as `raw.files` before all 47 reports declared it: a failed pull left the
    reports quietly re-reading yesterday.

    Six models gained a `platform.accounts` join when the conformed spine moved onto the account
    (see models/platform/accounts.sql); this is what says all six declared it.
    """
    sql = path.read_text()
    header = re.search(r"depends_on: \[(.*?)\]", sql, re.S)
    declared = set(re.findall(r"platform\.\w+", header.group(1))) if header else set()
    joined = {
        f"platform.{name}"
        for name in re.findall(r"JOIN\s+platform\.(\w+)\b", sql)
        if name != path.stem
    }
    assert joined <= declared, f"platform.{path.stem} joins {sorted(joined - declared)} undeclared"


def test_sql_line_comments_must_not_nest_block_comments() -> None:
    """interlace rewrites each `--` line as `/* … */` before DuckDB sees it, and DuckDB
    nests block comments. A glob like `PSM/model/assignment/*` in a header note is
    `Parser Error: unterminated /* comment` at apply-time and a green unit test that
    executed the `--` form. `docs/gotchas.md` records the trap."""
    offenders: list[str] = []
    for path in (ROOT / "models").rglob("*.sql"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.lstrip()
            if not stripped.startswith("--"):
                continue
            body = stripped[2:]
            if "/*" in body or "*/" in body:
                offenders.append(f"{path.relative_to(ROOT)}:{i}")
    assert not offenders, (
        "DuckDB nests /* comments after interlace rewrites -- lines; "
        f"remove /* and */ from these comments: {offenders}"
    )
