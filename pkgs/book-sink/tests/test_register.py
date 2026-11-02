"""What `register()` puts in the DAG, for two firms.

The shape is the whole design (docs/provider-pipeline.md §2), and three properties of it are load-
bearing enough to pin:

* **each tenant's models are DIFFERENT models.** interlace fingerprints a model from its SQL text,
  and closures are not part of it -- so if the org-key predicate were passed in rather than inlined,
  every firm's slice would be textually identical and a re-scoped firm would never rebuild.
* **the merge takes no parameters.** It declares the staging models as dependencies so it runs after
  them; a parameter would pull the whole slice back through Arrow to establish an ordering the graph
  already knows.
* **the delivery target is unquoted.** interlace splits a `materialise: table` target on `.` and
  quotes the parts itself, so a quoted name here would become part of the identifier.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from book_sink import SinkConfig, TenantSlice, register
from book_sink.register import _slice_key

interlace = pytest.importorskip("interlace.dsl.decorators")

PROVIDER = "seccl"


def _slice(uid: str, tenant_id: int, node: str) -> TenantSlice:
    return TenantSlice(
        enablement_id=tenant_id,
        tenant_id=tenant_id,
        uid=uid,
        name=f"Firm {tenant_id}",
        provider=PROVIDER,
        config={"node_ids": [node]},
        bucket=f"tnt-{uid}",
    )


@pytest.fixture
def registered() -> Any:
    """Two firms on different nodes, registered into a clean registry."""
    interlace.REGISTRY.clear()
    names = register(
        PROVIDER,
        config=SinkConfig(book_dsn="postgresql://book_writer@localhost/portals", datalake_dsn=""),
        slices=[_slice("aaa1", 1, "1"), _slice("bbb2", 2, "9")],
    )
    yield interlace.REGISTRY, names
    interlace.REGISTRY.clear()


def test_every_layer_is_registered_for_every_firm(registered: Any) -> None:
    registry, names = registered
    for uid in ("aaa1", "bbb2"):
        assert f"tenant_{uid}.accounts" in names, "the slice"
        assert f"stage_{uid}.accounts" in names, "the staging delivery"
        assert f"book_{uid}.merge_ledger" in names, "the merge"
        assert f"lake_{uid}.projection" in names, "the firm's own catalogue"
    assert set(names) == set(registry.models)


def test_the_lake_projection_waits_for_every_slice(registered: Any) -> None:
    """It reads CORE rather than the slices (a filter model's environment view is promoted at the
    END of an apply), but it must still run after the conformed models have — which is what
    depending on the slices buys, without depending on their promotion."""
    registry, _ = registered
    projection = registry.models["lake_aaa1.projection"]
    assert projection.fn is not None
    assert not inspect.signature(projection.fn).parameters
    assert set(projection.depends_on) == {
        name for name in registry.models if name.startswith("tenant_aaa1.")
    }


def test_a_firms_models_are_tagged_with_its_uid(registered: Any) -> None:
    """The tag IS the refresh selector: `POST /runs {"selectors": ["tag:tenant:{uid}"]}`."""
    registry, _ = registered
    for uid in ("aaa1", "bbb2"):
        tagged = {name for name, m in registry.models.items() if f"tenant:{uid}" in m.tags}
        assert tagged, f"no models tagged for {uid}"
        assert all(uid in name for name in tagged), "a tag must not reach another firm's models"


def test_held_back_ten_prefix_is_not_doubled_in_model_names() -> None:
    """`ten_<hex>` is the uid; the DAG schema is `tenant_<hex>`."""
    interlace.REGISTRY.clear()
    uid = "ten_3b54affd38444414"
    names = register(
        PROVIDER,
        config=SinkConfig(book_dsn="postgresql://book_writer@localhost/portals", datalake_dsn=""),
        slices=[_slice(uid, 1, "1")],
    )
    key = "3b54affd38444414"
    assert f"tenant_{key}.accounts" in names
    assert f"stage_{key}.accounts" in names
    assert f"book_{key}.merge_ledger" in names
    assert f"lake_{key}.projection" in names
    assert not any("tenant_ten_" in name or "inbound_" in name for name in names)
    tagged = {
        name for name, model in interlace.REGISTRY.models.items() if f"tenant:{uid}" in model.tags
    }
    assert tagged, "selectors still address the firm by its full uid"
    interlace.REGISTRY.clear()


def test_sei_valuations_are_keyed_by_the_account_and_the_day() -> None:
    """A valuation has no provider id. SEI must not slice on a synthetic external_id."""
    assert _slice_key("sei", "valuations") == ("account_external_id", "as_of_date")
    assert _slice_key("seccl", "valuations") == ("external_id", "as_of_date")


def test_tenant_slices_full_merge_so_apply_does_not_drop_them(registered: Any) -> None:
    """`replace` dropped the DuckLake table mid-apply; lake projection then found no `nodes`."""
    registry, _ = registered
    accounts = registry.models["tenant_aaa1.accounts"]
    assert accounts.strategy == "full_merge"
    assert accounts.key == ("external_id",)
    valuations = registry.models["tenant_aaa1.valuations"]
    assert valuations.key == ("external_id", "as_of_date")


def test_two_firms_slices_are_not_the_same_query(registered: Any) -> None:
    """The closure trap, pinned. Identical SQL would mean identical fingerprints, and interlace
    would consider one firm's slice already built because the other's was."""
    registry, _ = registered
    first = registry.models["tenant_aaa1.accounts"].sql
    second = registry.models["tenant_bbb2.accounts"].sql
    assert first != second
    assert "list_has_any(lineage, ['1'])" in first
    assert "list_has_any(lineage, ['9'])" in second


def test_every_slice_checks_the_runtime_reset_pause(registered: Any) -> None:
    """A daemon registers once, so a load-time exclusion cannot pause an already-loaded firm."""
    registry, _ = registered
    sql = registry.models["tenant_aaa1.accounts"].sql
    assert "book.platform.tenant_platforms" in sql
    assert "id = 1 AND refresh_paused_at IS NULL" in sql


def test_the_staging_model_delivers_into_the_book_unquoted(registered: Any) -> None:
    registry, _ = registered
    model = registry.models["stage_aaa1.accounts"]
    assert model.materialise == "table", "an external table interlace delivers into, never drops"
    assert model.strategy == "replace", "ReplaceInPlace: empty and re-fill, so grants survive"
    assert model.target == "book.inbound_seccl.aaa1__accounts"
    assert '"' not in (model.target or ""), "interlace quotes the parts itself"


def test_the_staging_model_waits_for_the_slices_it_reads(registered: Any) -> None:
    """accounts needs its valuation for `value`, so it depends on BOTH filters -- otherwise the
    balance would be read from a relation that had not been built yet."""
    registry, _ = registered
    depends = set(registry.models["stage_aaa1.accounts"].depends_on)
    assert depends == {"tenant_aaa1.accounts", "tenant_aaa1.valuations"}


def test_the_merge_runs_after_the_staging_models_and_takes_none_of_them(registered: Any) -> None:
    registry, _ = registered
    merge = registry.models["book_aaa1.merge_ledger"]
    assert merge.fn is not None
    assert not inspect.signature(merge.fn).parameters, "a parameter would re-read the whole slice"
    staged = {name for name in registry.models if name.startswith("stage_aaa1.")}
    assert set(merge.depends_on) == staged


def test_the_shelf_and_its_platform_reference_both_come_from_accounts(registered: Any) -> None:
    """Two entities, one source relation -- and therefore two landing tables, which is what the
    entity's own name is for."""
    registry, _ = registered
    for entity in ("products", "product_platforms"):
        model = registry.models[f"stage_aaa1.{entity}"]
        assert model.target == f"book.inbound_seccl.aaa1__{entity}"
        assert model.depends_on == ("tenant_aaa1.accounts",)


def test_an_unregisterable_tenant_does_not_take_the_project_down(registered: Any) -> None:
    """A firm with a malformed enablement is a configuration problem, not a reason the provider's
    own models stop building."""
    registry, _ = registered
    interlace.REGISTRY.clear()
    names = register(
        PROVIDER,
        config=SinkConfig(book_dsn="postgresql://book_writer@localhost/x", datalake_dsn=""),
        slices=[_slice("aaa1", 1, "1"), TenantSlice(2, 2, "bad2", "Bad", PROVIDER, {}, "tnt-bad2")],
    )
    assert any("aaa1" in name for name in names), "the good firm still registers"
    assert not any("bad2" in name for name in names), "the malformed one is skipped, not fatal"


def test_a_uid_too_long_to_name_is_refused_not_truncated() -> None:
    """Postgres truncates an identifier over 63 bytes to exactly 63, silently. A real uid is
    `ten_` + 32 hex = 36 characters and `{uid}__third_party_providers` is 59 — four bytes of
    headroom. At 40 two entities would truncate to the SAME table name, one firm's staged accounts
    quietly becoming its shelf. Refused where the name is built, so it fails as a skipped tenant
    with a reason rather than as data that looks fine."""
    from book_sink.merge import MergeUnavailable, landing_relation, landing_schema

    # The provider lives in the SCHEMA. The table name is already 60 of 63 bytes, so a provider
    # token there would truncate two entities onto one table.
    assert landing_schema("morgan-lloyd") == "inbound_morgan_lloyd"
    relation = landing_relation("ten_" + "a" * 32, "third_party_providers", "seccl")
    assert relation.startswith("inbound_seccl.")
    assert relation.endswith("third_party_providers")
    assert len(relation.split(".", 1)[1].encode()) <= 63
    with pytest.raises(MergeUnavailable, match="Postgres truncates"):
        landing_relation("t" * 45, "third_party_providers", "seccl")
