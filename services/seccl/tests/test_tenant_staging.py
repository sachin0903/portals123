"""The per-tenant models bind against the REAL conformed layer, not against a stub of it.

`pkgs/book-sink` tests its projections against stub relations, which proves it is self-consistent.
This proves the two sides actually meet: the models in `models/platform/*.sql` are executed exactly
as `test_platform_models.py` executes them, and then the filter predicate and the staging projection
are run on top.

That is the failure worth catching here. A column the sink expects and Seccl renamed is a DuckDB
Binder Error which aborts the whole tenant, and the ledger records it as "unavailable" -- which
reads identically to "this firm has no data with this provider". Same class as the SEI fan-out
that aborted on `Binder Error: Referenced column "subfirm_id" not found`.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlglot
from book_sink import entities, predicate
from test_platform_models import (
    BUILD_ORDER,
    DERIVED_BUILD_ORDER,
    RAW_TABLES,
    _derived_sql,
    _model_sql,
)

duckdb = pytest.importorskip("duckdb")

PROVIDER = "seccl"
#: A tenant on one node, which is what a real enablement looks like.
CONFIG = {"node_ids": ["1"]}


@pytest.fixture(scope="module")
def warehouse() -> Any:
    connection = duckdb.connect(":memory:")
    for schema in ("raw", "staging", "derived", "platform"):
        connection.execute(f"CREATE SCHEMA {schema}")
    for table, columns in RAW_TABLES.items():
        connection.execute(f"CREATE TABLE {table} ({columns})")
    for name in DERIVED_BUILD_ORDER:
        connection.execute(f"CREATE TABLE derived.{name} AS {_derived_sql(name)}")
    for name in BUILD_ORDER:
        connection.execute(f"CREATE TABLE platform.{name} AS {_model_sql(name)}")
    return connection


def _relations() -> dict[str, str]:
    return {name: f"platform.{name}" for name in BUILD_ORDER}


def test_seccl_publishes_every_relation_the_sink_projects() -> None:
    """The sink names relations; the models provide them. A rename on either side is a missing
    table at run time, which the ledger cannot tell apart from an empty slice."""
    missing = set(entities.lake_relations(PROVIDER)) - set(BUILD_ORDER)
    assert not missing, f"book_sink expects {sorted(missing)}, which Seccl does not publish"


@pytest.mark.parametrize("relation", entities.lake_relations(PROVIDER))
def test_the_tenant_filter_binds_against_the_conformed_relation(
    warehouse: Any, relation: str
) -> None:
    """One containment predicate, every relation -- including the reference tables whose lineage is
    empty. A relation that does not carry lineage would raise here rather than in production."""
    where = predicate.for_provider(PROVIDER, CONFIG, relation)
    warehouse.execute(f"SELECT count(*) FROM platform.{relation} WHERE {where}")  # noqa: S608


@pytest.mark.parametrize("entity", entities.for_provider(PROVIDER), ids=lambda e: e.slug)
def test_the_staging_projection_survives_sqlglot_and_binds(
    warehouse: Any, entity: entities.Entity
) -> None:
    """Executed as INTERLACE would execute it: parsed and re-rendered by sqlglot first.

    Running the projection's own text proves it works in DuckDB, which is not the same thing. A
    two-argument lambda — `list_transform(l, (x, i) -> …)` — is valid DuckDB and round-trips as
    `main."row"(x, i) -> …`, a Binder Error that appears at APPLY time and nowhere else: not in a
    plan, not in a direct-DuckDB test. That cost a real run to find.
    """
    sql = sqlglot.parse_one(
        entity.staging_sql(tenant_id=1, provider=PROVIDER, relations=_relations()),
        dialect="duckdb",
    ).sql(dialect="duckdb")
    columns = {
        row[0]
        for row in warehouse.execute(f"DESCRIBE ({sql})").fetchall()  # noqa: S608
    }
    assert columns, f"{entity.slug} staged nothing"
    for parent in entity.parents:
        if not parent.source:
            continue  # `platform` needs no key from the provider: the provider IS the key
        assert parent.source in columns, f"{entity.slug} does not stage {parent.source}"
    assert entity.row_key in columns, f"{entity.slug} cannot name its own rows in the ledger"


def _staged_rows(entity_slug: str, models: list[tuple[str, str | None, str | None]]) -> list[Any]:
    """Run one entity's projection over real model rows, on its own connection.

    Binding is not evaluating. A JSON function against an empty table is never asked to parse
    anything, so a table-shaped fixture with no rows cannot see a value-dependent failure — which is
    how `json_extract_string(commentary, '$')` reached a real apply and aborted the whole entity on
    `Malformed JSON at byte 0`. Rows here, deliberately, and the three commentary shapes the book
    really holds.
    """
    connection = duckdb.connect(":memory:")
    for schema in ("raw", "staging", "derived", "platform"):
        connection.execute(f"CREATE SCHEMA {schema}")
    for table, columns in RAW_TABLES.items():
        connection.execute(f"CREATE TABLE {table} ({columns})")
    for name in DERIVED_BUILD_ORDER:
        connection.execute(f"CREATE TABLE derived.{name} AS {_derived_sql(name)}")
    for external_id, allocations, commentary in models:
        connection.execute(
            "INSERT INTO raw.models (id, node_id, name, status, cash_allocation,"
            " allocations, commentary) VALUES (?, '1', 'A Model', 'Active', '0.05', ?, ?)",
            [external_id, allocations, commentary],
        )
    for name in BUILD_ORDER:
        connection.execute(f"CREATE TABLE platform.{name} AS {_model_sql(name)}")
    entity = next(e for e in entities.for_provider(PROVIDER) if e.slug == entity_slug)
    sql = sqlglot.parse_one(
        entity.staging_sql(tenant_id=1, provider=PROVIDER, relations=_relations()), dialect="duckdb"
    ).sql(dialect="duckdb")
    return connection.execute(f"SELECT * FROM ({sql}) ORDER BY 2").fetchall()  # noqa: S608


def test_a_commentary_is_read_whichever_shape_the_provider_sends() -> None:
    """Seccl sends plain free text; a JSON-encoded string is equally valid on the conformed column.
    Both are somebody's note. A structured value is not, and must not be serialised into a markdown
    body that a client reads."""
    rows = _staged_rows(
        "model_commentary",
        [
            ("M1", None, "This is a sample commentary about the ETC"),
            ("M2", None, '"quoted by the provider"'),
            ("M3", None, '{"author": "an object, not a note"}'),
            ("M4", None, "   "),
            ("M5", None, None),
        ],
    )
    assert [(row[1], row[2]) for row in rows] == [
        ("M1", "This is a sample commentary about the ETC"),
        ("M2", "quoted by the provider"),
    ]


def test_the_allocation_lines_carry_their_order_and_drop_what_is_not_held() -> None:
    """The index comes from two parallel unnests rather than a lambda (see `_MODEL_LINES_FROM`), so
    an off-by-one or a swapped column would land silently. A withdrawn line stays in the payload."""
    rows = _staged_rows(
        "model_allocations",
        [
            (
                "M1",
                '[{"assetId": "A1", "percentage": 0.245, "status": "Active"},'
                ' {"assetId": "GONE", "percentage": 0.05, "status": "Withdrawn"},'
                ' {"assetId": "A2", "percentage": 0.70}]',
                None,
            )
        ],
    )
    lines = [(row[2], float(row[5]), row[6]) for row in rows]
    # 0 and 2, not 0 and 1: `sort_order` is the line's position in the payload the provider
    # published, and the withdrawn line occupied position 1. The gap is what app/materialise.py
    # produces too (`enumerate(raw)` runs before the status filter), and the column is only ever
    # read as an ORDER BY, so renumbering would break parity to no end.
    assert lines == [("A1", 24.5, 0), ("A2", 70.0, 2)]
