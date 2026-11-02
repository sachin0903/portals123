"""The tenant-lake projection: the statements, and what they must not get wrong.

Asserted as statements rather than executed, for the same reason `app/lake/attach.py` is: this
leg's failure mode does not raise. A wrong endpoint, a wrong metadata schema or a missing
`URL_STYLE` lands the parquet where the reader never looks — which reads as "the catalogue is
empty", and that is indistinguishable from "this firm has no data with this provider".
"""

from __future__ import annotations

import pytest

from book_sink import SinkConfig, SinkUnavailable, lake

SETTINGS = SinkConfig(
    book_dsn="postgresql://book_writer@localhost/portals",
    datalake_dsn="dbname=datalakes host=db user=lake_writer",
    s3_endpoint="http://rustfs:9000",
    s3_access_key_id="key",
    s3_secret_access_key="secret",  # noqa: S106 - a test credential is the point
)


def _statements(**kwargs: str) -> list[str]:
    return lake.attach_statements(
        SETTINGS, **{"uid": "abc123", "bucket": "tnt-abc123", "lake_schema": "seccl", **kwargs}
    )


def test_the_core_catalogue_is_attached_read_only() -> None:
    """The projection reads core and writes the tenant. READ_ONLY makes that structural rather than
    a convention this function has to keep."""
    attach = next(s for s in _statements() if " AS core " in s)
    assert "METADATA_SCHEMA 'seccl'" in attach
    assert "READ_ONLY" in attach


def test_the_tenant_catalogue_gets_its_own_bucket_and_schema() -> None:
    """The two options interlace's `attach:` cannot express, which is the whole reason this leg is a
    Python model: DATA_PATH (the parquet is the tenant's) and METADATA_SCHEMA (the catalogue is)."""
    attach = next(s for s in _statements() if " AS tenant " in s)
    assert "DATA_PATH 's3://tnt-abc123/datalake/'" in attach
    assert "METADATA_SCHEMA 'abc123'" in attach
    assert "READ_ONLY" not in attach


def test_a_provider_is_named_by_its_catalogue_schema_not_its_own_name() -> None:
    """Morgan Lloyd's catalogue attaches as `ml`. Reading `morgan_lloyd` is how its materialise came
    to read an empty schema and record it as "this tenant has no data"."""
    attach = next(s for s in _statements(lake_schema="ml") if " AS core " in s)
    assert "METADATA_SCHEMA 'ml'" in attach


def test_the_object_store_secret_uses_path_style() -> None:
    """A bucket-per-tenant on a self-hosted store has no DNS for `tnt-abc123.host`, so virtual-host
    addressing does not resolve at all."""
    secret = next(s for s in _statements() if s.startswith("CREATE SECRET"))
    assert "URL_STYLE 'path'" in secret
    assert "ENDPOINT 'rustfs:9000'" in secret, "the scheme is stripped; DuckDB wants host[:port]"
    assert "USE_SSL false" in secret


def test_https_endpoints_keep_ssl_on() -> None:
    settings = SinkConfig(
        book_dsn="x", datalake_dsn="dbname=datalakes", s3_endpoint="https://s3.example.com"
    )
    secret = next(
        s
        for s in lake.attach_statements(settings, uid="a", bucket="b", lake_schema="seccl")
        if s.startswith("CREATE SECRET")
    )
    assert "USE_SSL true" in secret


def test_no_datalake_dsn_is_refused_rather_than_guessed() -> None:
    settings = SinkConfig(book_dsn="x", datalake_dsn="")
    with pytest.raises(SinkUnavailable):
        lake.attach_statements(settings, uid="a", bucket="b", lake_schema="seccl")


def test_the_extensions_load_before_anything_needs_them() -> None:
    statements = _statements()
    assert all(ext in statements[i] for i, ext in enumerate(("ducklake", "postgres", "httpfs")))
    assert statements[3].startswith("CREATE SECRET"), "the secret must precede the S3 attach"


def test_a_projection_replaces_rather_than_accumulates() -> None:
    """A row the provider stopped publishing must stop being in the firm's lake, so the tenant table
    is the slice as it stands and not a running total of every slice ever projected."""
    sql = lake.projection_sql("accounts", "(len(lineage) = 0 OR list_has_any(lineage, ['1']))")
    assert sql.startswith("CREATE OR REPLACE TABLE tenant.platform.accounts AS")
    assert "FROM core.platform.accounts WHERE" in sql
    assert "list_has_any(lineage, ['1'])" in sql


def test_the_manifest_names_every_relation_it_projected() -> None:
    rows = lake.manifest_rows("abc123", "seccl", {"accounts": 376, "nodes": 50}, "2026-08-06")
    # Sorted, so a diff between two runs is readable.
    assert [row["relation"] for row in rows] == ["accounts", "nodes"]
    assert rows[0]["rows"] == 376
    assert {row["tenant_uid"] for row in rows} == {"abc123"}


def test_a_missing_core_table_does_not_abort_the_other_relations() -> None:
    """A full SEI apply rebuilds virtual `platform.nodes` and then projects the tenant lake in the
    same run. The lake model ATTACHes the catalogue, where that table is not promoted until the
    apply finishes — so `nodes does not exist` used to fail the whole run (8 and 11) before the
    promotion that would have put the table back."""
    duckdb = pytest.importorskip("duckdb")
    connection = duckdb.connect(":memory:")
    connection.execute("ATTACH ':memory:' AS core")
    connection.execute("ATTACH ':memory:' AS tenant")
    connection.execute("CREATE SCHEMA core.platform")
    connection.execute("CREATE SCHEMA tenant.platform")
    connection.execute("CREATE TABLE core.platform.accounts AS SELECT 1 AS id")
    counts = lake.project_relations(connection, {"nodes": "true", "accounts": "true"})
    assert counts == {"accounts": 1}
    assert lake.is_missing_relation_error(
        RuntimeError("Catalog Error: Table with name nodes does not exist!")
    )
    assert not lake.is_missing_relation_error(
        RuntimeError('Binder Error: Referenced column "subfirm_id" not found')
    )
