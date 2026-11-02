"""Register one provider's per-tenant models: filter -> stage -> merge.

Four layers per enabled tenant, and each is a real node in the provider's DAG rather than a side
job the API kicks off. Tenant uids are ``ten_<hex>``; DAG names strip that prefix so a firm is
``tenant_<hex>``, not ``tenant_ten_<hex>``.

  tenant_{hex}.{entity}    virtual  the tenant's slice of the conformed relation (one containment
                                    predicate, the tenant's org keys INLINED so the fingerprint is
                                    the tenant's own -- interlace hashes the SQL text, not the
                                    closure, so a shared predicate would make every tenant's model
                                    identical and a re-scoped firm would never rebuild)
  stage_{hex}.{entity}     table    the book's own column names, delivered into
                                    inbound.{uid}__{entity} in Postgres over the attached `book`
                                    database (reverse ETL: interlace never drops an external table).
                                    These stay: the Python merge opens Postgres, not the in-session
                                    DuckDB that holds unpromoted slices, so it cannot read them.
  book_{hex}.merge_ledger  virtual  the merge itself, and its ledger as a warehouse table
  lake_{hex}.projection    virtual  the firm's OWN catalogue, refreshed, and its manifest

WHY THE MERGE IS A PYTHON MODEL WITH NO PARAMETERS: it declares its staging models as dependencies
so it runs after them, but it must not receive them -- a parameter would pull the whole slice back
through Arrow to establish an ordering the graph already knows. What it does read is the book, as
`book_writer`, which is the credential this whole design exists to keep narrow (0062).

A NEW TENANT IS PICKED UP ON THE NEXT REQUEST. `models/tenants.py` installs
`tenant_reload`, which recompiles when the enabled-firm set differs from the compiled
`tenant:` tags. Until that recompile, Refresh's `tag:tenant:{uid}` matches nothing and
interlace answers 400.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from book_sink import entities as entity_defs
from book_sink import merge as merge_mod
from book_sink import predicate
from book_sink.config import SinkConfig, from_env
from book_sink.tenants import TenantSlice, enabled

logger = logging.getLogger("book_sink.register")

#: The alias the provider's interlace.yaml must ATTACH the portals database under.
BOOK_ALIAS = "book"


def tenant_model_key(uid: str) -> str:
    """The tenant identity used in DAG / DuckLake names.

    Tenant uids are ``ten_<hex>``. Prefixing ``tenant_`` onto that produced
    ``tenant_ten_<hex>``. The held-back prefix stays on the uid itself (S3, Postgres
    landing tables); model names strip it.
    """
    return uid.removeprefix("ten_")


def _slice_key(provider: str, relation: str) -> tuple[str, ...]:
    """Grain of a tenant slice — the same key the conformed model publishes.

    ``full_merge`` needs this; the default ``replace`` dropped the DuckLake table mid-apply,
    which is what made ``lake_{hex}.projection`` report ``nodes does not exist``.
    """
    if relation == "valuations" and provider == "sei":
        # No synthetic external_id. The day is its own column; the book turns
        # account_external_id into account_id.
        return ("account_external_id", "as_of_date")
    if relation == "valuations":
        return ("external_id", "as_of_date")
    if relation == "tenancy":
        return ("account_number",)
    if relation == "dsr_grants":
        return ("scope", "grantee", "subject_id", "dsr_type_id", "dsr_id")
    return ("external_id",)


def provider_schema(provider: str) -> str:
    """The lake schema a provider's data lives under: morgan_lloyd | seccl | sei.

    DERIVED since 0134. It held `{"morgan-lloyd": "ml"}` because ML's catalogue attached as `ml`,
    so `morgan_lloyd` named a schema nothing published -- the exact mismatch that made the ML
    materialise read an empty schema and record it as "no data", which is indistinguishable from a
    firm that genuinely has none. The catalogue is named for the provider now, so the map is gone.

    What is left is an ENCODING rather than a second name: `-` is not legal in an unquoted SQL
    identifier. Mirrors `app/providers.py::provider_schema`.
    """
    return provider.replace("-", "_")


def _filter_name(uid: str, relation: str) -> str:
    return f"tenant_{tenant_model_key(uid)}.{relation}"


def _stage_name(uid: str, relation: str) -> str:
    return f"stage_{tenant_model_key(uid)}.{relation}"


def _ledger_name(uid: str) -> str:
    return f"book_{tenant_model_key(uid)}.merge_ledger"


def _lake_name(uid: str) -> str:
    return f"lake_{tenant_model_key(uid)}.projection"


def _refresh_paused(config: SinkConfig, enablement_id: int) -> bool:
    """Read the operational reset gate at execution time.

    Registration happens once at daemon startup, so excluding a paused tenant in
    ``tenants.enabled`` is insufficient for a tenant paused afterwards.  The lake
    projection reads core relations directly and therefore needs this runtime
    check as well as the SQL gate on the filtered relations below.
    """
    import psycopg

    with psycopg.connect(config.require_book()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT refresh_paused_at IS NOT NULL FROM platform.tenant_platforms WHERE id = %s",
            (enablement_id,),
        )
        row = cur.fetchone()
    return row is None or bool(row[0])


def _merge_fn(slice_: TenantSlice, provider: str, config: SinkConfig) -> Callable[[], Any]:
    """The merge, as a Python model's function. Returns its ledger as Arrow."""

    def run_merge() -> Any:
        # Imported inside the model so the package stays importable (and `plan` stays fast) on a
        # machine that has no book to write.
        import psycopg
        import pyarrow as pa

        if _refresh_paused(config, slice_.enablement_id):
            return pa.Table.from_pylist(
                [
                    {
                        "tenant_uid": slice_.uid,
                        "provider": provider,
                        "entity": "all",
                        "created": 0,
                        "updated": 0,
                        "rejected": 0,
                        "reasons": "{}",
                        "note": "tenant refresh paused",
                        "merged_at": datetime.now(UTC),
                    }
                ],
                schema=pa.schema(
                    [
                        ("tenant_uid", pa.string()),
                        ("provider", pa.string()),
                        ("entity", pa.string()),
                        ("created", pa.int64()),
                        ("updated", pa.int64()),
                        ("rejected", pa.int64()),
                        ("reasons", pa.string()),
                        ("note", pa.string()),
                        ("merged_at", pa.timestamp("us", tz="UTC")),
                    ]
                ),
            )

        with psycopg.connect(config.require_book(), autocommit=True) as conn:
            ledger = merge_mod.merge_tenant(
                conn,
                entity_defs.for_provider(provider),
                uid=slice_.uid,
                tenant_id=slice_.tenant_id,
                provider=provider,
                enablement_id=slice_.enablement_id,
            )

        rows = [
            {
                "tenant_uid": slice_.uid,
                "provider": provider,
                "entity": record.get("entity"),
                "created": int(record.get("created", 0)),
                "updated": int(record.get("updated", 0)),
                "rejected": int(record.get("rejected", 0)),
                # JSON rather than a map type: the reasons are free text from the provider's own
                # vocabulary and a struct would fix their shape at the first one we saw.
                "reasons": json.dumps(record.get("reasons") or {}),
                "note": record.get("note") or record.get("error"),
                "merged_at": datetime.now(UTC),
            }
            for record in ledger
        ]
        return pa.Table.from_pylist(
            rows,
            schema=pa.schema(
                [
                    ("tenant_uid", pa.string()),
                    ("provider", pa.string()),
                    ("entity", pa.string()),
                    ("created", pa.int64()),
                    ("updated", pa.int64()),
                    ("rejected", pa.int64()),
                    ("reasons", pa.string()),
                    ("note", pa.string()),
                    ("merged_at", pa.timestamp("us", tz="UTC")),
                ]
            ),
        )

    return run_merge


def _lake_fn(slice_: TenantSlice, provider: str, config: SinkConfig) -> Callable[[], Any]:
    """The tenant-lake projection, as a Python model's function. Returns its manifest as Arrow."""

    def run_projection() -> Any:
        import pyarrow as pa

        from book_sink import lake

        if _refresh_paused(config, slice_.enablement_id):
            return pa.Table.from_pylist(
                [],
                schema=pa.schema(
                    [
                        ("tenant_uid", pa.string()),
                        ("provider", pa.string()),
                        ("relation", pa.string()),
                        ("rows", pa.int64()),
                        ("projected_at", pa.timestamp("us", tz="UTC")),
                    ]
                ),
            )

        predicates = {
            relation: predicate.for_provider(provider, slice_.config, relation)
            for relation in entity_defs.lake_relations(provider)
        }
        counts = lake.project(
            config,
            uid=slice_.uid,
            bucket=slice_.bucket,
            lake_schema=provider_schema(provider),
            predicates=predicates,
        )
        return pa.Table.from_pylist(
            lake.manifest_rows(slice_.uid, provider, counts, datetime.now(UTC)),
            schema=pa.schema(
                [
                    ("tenant_uid", pa.string()),
                    ("provider", pa.string()),
                    ("relation", pa.string()),
                    ("rows", pa.int64()),
                    ("projected_at", pa.timestamp("us", tz="UTC")),
                ]
            ),
        )

    return run_projection


def register(
    provider: str,
    *,
    config: SinkConfig | None = None,
    slices: list[TenantSlice] | None = None,
) -> list[str]:
    """Register every model for every enabled tenant. Returns the model names, for tests and logs.

    Import-time work, called from the provider's `models/tenants.py`. Registration failures for one
    tenant must not take the project down with them -- a firm with a malformed enablement is a
    configuration problem, not a reason the provider's own models stop building.
    """
    from interlace.dsl.decorators import REGISTRY, ModelDef

    settings = config or from_env()
    registered: list[str] = []
    tenant_slices = slices if slices is not None else enabled(provider, settings)
    merged = entity_defs.for_provider(provider)
    lake_relations = entity_defs.lake_relations(provider)

    for slice_ in tenant_slices:
        tag = slice_.tag
        try:
            filters: dict[str, str] = {}
            for relation in lake_relations:
                where = predicate.for_provider(provider, slice_.config, relation)
                # Runtime gate as well as load-time exclusion: a daemon may have
                # registered this tenant before an operator started a reset.
                where = (
                    f"({where}) AND EXISTS (SELECT 1 FROM {BOOK_ALIAS}.platform.tenant_platforms "  # noqa: S608 - fixed alias and integer id
                    f"WHERE id = {slice_.enablement_id} AND refresh_paused_at IS NULL)"
                )
                name = _filter_name(slice_.uid, relation)
                source = f"{entity_defs.PLATFORM_SCHEMA}.{relation}"
                REGISTRY.register_model(
                    ModelDef(
                        name=name,
                        # The predicate is INLINED, not passed: interlace fingerprints a model from
                        # its SQL text, so this is what makes one tenant's slice a different model
                        # from another's -- and what makes a re-scoped firm rebuild.
                        sql=f"SELECT * FROM {source} WHERE {where}",  # noqa: S608 - own registry
                        strategy="full_merge",
                        key=_slice_key(provider, relation),
                        depends_on=(source,),
                        tags=(tag, provider, "tenant", "slice"),
                        description=f"{slice_.name}: its slice of {source}.",
                    )
                )
                filters[relation] = name
                registered.append(name)

            staged: list[str] = []
            for entity in merged:
                name = _stage_name(slice_.uid, entity.slug)
                REGISTRY.register_model(
                    ModelDef(
                        name=name,
                        sql=entity.staging_sql(
                            tenant_id=slice_.tenant_id, provider=provider, relations=filters
                        ),
                        materialise="table",
                        # `replace` on a terminal table is ReplaceInPlace: it empties and re-fills
                        # the landing table without ever dropping it, which is what keeps
                        # book_writer's grants and any reader intact.
                        strategy="replace",
                        # The UNQUOTED form: interlace splits a target on `.` and quotes the
                        # parts itself.
                        target=(
                            f"{BOOK_ALIAS}."
                            f"{merge_mod.landing_relation(slice_.uid, entity.slug, provider)}"
                        ),
                        depends_on=tuple(filters[rel] for rel in entity.relations()),
                        tags=(tag, provider, "tenant", "stage"),
                        description=f"{slice_.name}: {entity.slug} staged for the book.",
                    )
                )
                staged.append(name)
                registered.append(name)

            if filters:
                # The tenant's own parquet. Depends on the SLICES so it runs after the conformed
                # models, but reads core itself (see book_sink.lake): a filter model's environment
                # view is promoted at the END of an apply, so reading it would make this leg's
                # correctness depend on when it happened to run.
                projection = _lake_name(slice_.uid)
                REGISTRY.register_model(
                    ModelDef(
                        name=projection,
                        fn=_lake_fn(slice_, provider, settings),
                        depends_on=tuple(filters.values()),
                        tags=(tag, provider, "tenant", "lake"),
                        description=f"{slice_.name}: its own catalogue, refreshed.",
                    )
                )
                registered.append(projection)

            if staged:
                ledger = _ledger_name(slice_.uid)
                REGISTRY.register_model(
                    ModelDef(
                        name=ledger,
                        fn=_merge_fn(slice_, provider, settings),
                        # Depends on the staging models so it runs after them; takes none of them as
                        # parameters, so nothing is read back through Arrow.
                        depends_on=tuple(staged),
                        tags=(tag, provider, "tenant", "merge"),
                        description=f"{slice_.name}: what the merge into the book did.",
                    )
                )
                registered.append(ledger)
        except Exception:
            logger.warning(
                "book_sink: could not register models for tenant %s (%s)",
                slice_.uid,
                provider,
                exc_info=True,
            )
    return registered
