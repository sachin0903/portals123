"""A dataset must never resolve from dev fixture data.

``app/lake/fixture.py`` serves a committed extract (``docs/seccl.duckdb``) when the warehouse is
unreadable, and that is deliberate for the DISPLAY seam: a client signing in to an empty holdings
table cannot tell "we hold nothing for you" from "the pipeline has not run", so the portal shows
the extract rather than a flat chart beside a real balance.

A DOCUMENT is the other side of that line. Its numbers are sent to a client and kept, so a dataset
that degraded to fixture data would put a fabricated valuation in a client's statement — and the
extract's rows are real-looking (an eleven-month-stale £844,619 carbon ETC), so nothing downstream
would look wrong.

The existing guard in ``test_lake_holdings.py`` scans for the string literal ``seccl.duckdb`` and
allowlists ``app/lake/fixture.py``. That cannot see this failure: a resolver reaching the fixture
THROUGH ``routers/crm.py::_firm_read`` (which catches the warehouse failure and falls back) or
through ``lake.fixture`` itself names no such literal and passes green. So this scans the render
package's imports instead.
"""

from __future__ import annotations

import ast
import pathlib

#: Modules a dataset resolves through. Everything reachable from a queued render lives here.
_RENDER_DIRS = ("app/render",)

#: What none of them may reach. `crm` is named because `_firm_read` is the DISPLAY seam — it
#: degrades to the fixture on purpose, and a document must not inherit that.
_FORBIDDEN_MODULES = ("app.lake.fixture", "app.routers.crm")
_FORBIDDEN_NAMES = ("_firm_read", "reader_for")


def _api_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


def _render_modules() -> list[pathlib.Path]:
    root = _api_root()
    out: list[pathlib.Path] = []
    for rel in _RENDER_DIRS:
        out.extend(sorted((root / rel).rglob("*.py")))
    return out


def test_the_render_path_exists_and_is_being_scanned() -> None:
    # A scan over an empty file list passes for the wrong reason — the failure mode this whole
    # module is written against.
    modules = _render_modules()
    assert modules, f"no modules found under {_RENDER_DIRS} — this tripwire is scanning nothing"
    assert any(p.name == "resolve.py" for p in modules)
    assert any(p.name == "catalogue.py" for p in modules)


def test_no_render_module_imports_the_dev_fixture_or_the_display_seam() -> None:
    offenders: list[str] = []
    for path in _render_modules():
        rel = path.relative_to(_api_root()).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(m) for m in _FORBIDDEN_MODULES):
                        offenders.append(f"{rel}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if any(module.startswith(m) for m in _FORBIDDEN_MODULES):
                    offenders.append(f"{rel}: from {module}")
                # `from app.lake import fixture` — the module is the package, the name is the leaf.
                if module in {"app.lake", "app.routers"}:
                    for alias in node.names:
                        if alias.name in {"fixture", "crm"}:
                            offenders.append(f"{rel}: from {module} import {alias.name}")
                for alias in node.names:
                    if alias.name in _FORBIDDEN_NAMES:
                        offenders.append(f"{rel}: from {module} import {alias.name}")
    assert not offenders, (
        "a dataset would resolve from dev fixture data, which in a document is a fabricated "
        f"valuation sent to a client: {sorted(set(offenders))}"
    )


def test_no_render_module_names_the_fixture_reader() -> None:
    """The attribute form too: `lake.fixture.available()` after `from app import lake`."""
    offenders: list[str] = []
    for path in _render_modules():
        rel = path.relative_to(_api_root()).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"fixture", *_FORBIDDEN_NAMES}:
                offenders.append(f"{rel}: …{ast.unparse(node)}")
    assert not offenders, f"these reach the dev fixture by attribute: {sorted(set(offenders))}"


def test_the_catalogue_reads_only_the_oltp_book() -> None:
    """Every shipped entry queries Postgres. None reads the lake.

    The lake is not merely a different store here — it is a different SECURITY MODEL. Its
    credential (`lake_reader`) reaches the whole warehouse with no row scoping, the render container
    holds no lake credential at all, and the only tenant boundary is a schema name interpolated
    after a regex check. None of the envelope in `resolve.py::_execute_scoped` — the role downgrade,
    the minted claims, the read-only transaction, the timeout, the row cap, the post-statement
    principal re-check — exists on a DuckDB connection. So a lake-backed entry cannot claim to
    inherit any of it, and would need its own argument written from scratch.
    """
    import re

    from app.render import catalogue as cat

    # `tenant_` followed by anything but `id`: the lake's per-firm schema is `tenant_{uid}`, while
    # `a.tenant_id` is the OLTP column every entry legitimately filters on.
    lake_schema = re.compile(r"tenant_(?!id)")
    for entry in cat.all_entries():
        sql = entry.sql.lower()
        assert not lake_schema.search(sql), f"{entry.key} names a lake schema"
        assert "ducklake" not in sql and "attach" not in sql, f"{entry.key} reaches the lake"
        # Every entry reads a real OLTP schema, so a typo'd relation cannot pass as "no data".
        assert any(s in sql for s in ("platform.", "crm.", "documents.")), (
            f"{entry.key} reads no known OLTP schema"
        )
