"""The merge rules, which are the whole reason this package has a generator step.

Every type in `seccl.models` comes from these decisions, and they are the kind that fail
quietly: a field wrongly marked required rejects a payload Seccl accepts, and an inferred
enum rejects a value Seccl sends. So each rule is pinned here rather than trusted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_schemas as bs  # noqa: E402


def test_required_means_present_in_every_example() -> None:
    schema = bs.infer([{"a": 1, "b": 2}, {"a": 1}, {"a": 1, "b": 3}])
    assert schema["required"] == ["a"]
    assert set(schema["properties"]) == {"a", "b"}


def test_a_field_seen_once_is_still_a_field() -> None:
    """An absent key in one example is not evidence the field does not exist."""
    schema = bs.infer([{"a": 1}, {"b": 2}])
    assert set(schema["properties"]) == {"a", "b"}
    assert "required" not in schema


def test_updates_force_everything_optional_except_the_concurrency_token() -> None:
    values = [{"updateId": "1", "name": "x"}, {"updateId": "2", "name": "y"}]
    assert bs.infer(values, force_optional=True)["required"] == ["updateId"]
    # Without the flag, `name` is present in both and so would be required.
    assert bs.infer(values)["required"] == ["name", "updateId"]


def test_observed_values_are_documented_never_enumerated() -> None:
    """A closed enum built from a handful of examples rejects valid production values."""
    schema = bs.infer(["Active", "Pending", "Active"], name="status")
    assert "enum" not in schema
    assert schema["description"] == "observed values: Active, Pending"


@pytest.mark.parametrize(
    "values",
    [
        ["a-very-long-free-text-account-name-that-tells-nobody-anything"],
        [f"id-{i}" for i in range(40)],
    ],
)
def test_value_sets_that_are_data_not_vocabulary_go_undocumented(values: list[str]) -> None:
    assert "description" not in bs.infer(values, name="name")


def test_plain_dates_become_dates() -> None:
    assert bs.infer(["2026-01-01", "2026-02-02"], name="openDate")["format"] == "date"


def test_offset_carrying_timestamps_become_date_times() -> None:
    values = ["2022-09-12T00:00:00.000Z", "2023-01-01T09:30:00+01:00"]
    assert bs.infer(values, name="createdDate")["format"] == "date-time"


def test_a_timestamp_typed_as_a_date_would_reject_every_timestamp() -> None:
    """`date.fromisoformat` accepts a full timestamp since 3.11 and silently drops the
    time, which is how `paymentDate` came out as a date and rejected what Seccl sends."""
    assert bs.infer(["2026-01-01T09:30:00Z"], name="paymentDate")["format"] == "date-time"


def test_naive_timestamps_stay_strings() -> None:
    """`format: date-time` generates pydantic's AwareDatetime, which rejects these."""
    mixed = ["2022-09-12T00:00:00.000Z", "2022-09-12T00:00:00"]
    assert "format" not in bs.infer(mixed, name="declarationAcceptedDate")


def test_a_date_field_seen_only_as_a_placeholder_is_not_a_date() -> None:
    assert "format" not in bs.infer(["{{YYYY-MM-DD}}"], name="flexiAccessDate")


def test_placeholders_stay_out_of_documented_values() -> None:
    schema = bs.infer(["Active", "{{status}}"], name="status")
    assert schema["description"] == "observed values: Active"


def test_money_that_happens_to_be_round_is_still_a_float() -> None:
    assert bs.infer([100, 250], name="currentValue")["type"] == "number"


def test_count_shaped_names_keep_their_integers() -> None:
    assert bs.infer([1, 25], name="pageSize")["type"] == "integer"
    assert bs.infer([3], name="updateCount")["type"] == "integer"


def test_mixed_ints_and_floats_are_numbers() -> None:
    assert bs.infer([1, 2.5], name="rate")["type"] == "number"


def test_a_field_seen_as_null_and_a_value_is_nullable() -> None:
    assert bs.infer(["x", None], name="name")["type"] == ["string", "null"]


def test_a_field_only_ever_null_says_so_rather_than_guessing() -> None:
    assert bs.infer([None, None], name="name") == {"type": "null"}


def test_two_json_types_for_one_field_become_an_any_of() -> None:
    """Seccl sends `nodeId` as a scalar on one endpoint and a list on another."""
    schema = bs.infer(["9", ["9"]], name="nodeId")
    assert {branch["type"] for branch in schema["anyOf"]} == {"string", "array"}


def test_nested_objects_merge_instead_of_multiplying() -> None:
    """The bug this generator exists to prevent: one concept, one class."""
    samples = bs.Samples()
    samples.read = [{"wrapperDetail": {"wrapperType": "ISA", "advised": True}}]
    samples.create = [{"wrapperDetail": {"wrapperType": "GIA"}}]
    schema = bs.schema_for("account", samples)
    assert schema["properties"]["wrapperDetail"] == {"$ref": "#/$defs/WrapperDetail"}
    detail = schema["$defs"]["WrapperDetail"]
    assert set(detail["properties"]) == {"wrapperType", "advised"}
    # Pooled across shapes, so only what every observation carries is required.
    assert detail["required"] == ["wrapperType"]


def test_array_elements_are_named_in_the_singular() -> None:
    samples = bs.Samples()
    samples.read = [{"positions": [{"assetId": "a"}, {"assetId": "b"}]}]
    schema = bs.schema_for("account", samples)
    assert schema["properties"]["positions"]["items"] == {"$ref": "#/$defs/Position"}


def test_a_nullable_nested_object_keeps_its_null() -> None:
    samples = bs.Samples()
    samples.read = [{"client": {"id": "1"}}, {"client": None}, {"client": {"id": "2"}}]
    schema = bs.schema_for("account", samples)
    assert schema["properties"]["client"] == {
        "anyOf": [{"$ref": "#/$defs/Client"}, {"type": "null"}]
    }


def test_the_document_root_is_a_real_shape() -> None:
    """Given only `$defs`, datamodel-code-generator models the empty root as
    `RootModel[Any]` and renames the real shapes Account1, Account2."""
    samples = bs.Samples()
    samples.read = [{"id": "1"}]
    samples.create = [{"name": "x"}]
    schema = bs.schema_for("account", samples)
    assert schema["title"] == "Account"
    assert schema["properties"]["id"]["type"] == "string"
    assert "AccountCreate" in schema["$defs"]


def test_the_root_falls_back_to_a_write_shape_when_there_are_no_reads() -> None:
    samples = bs.Samples()
    samples.create = [{"name": "x"}]
    assert bs.schema_for("model", samples)["title"] == "ModelCreate"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"data": {"id": "1"}}, [{"id": "1"}]),
        ({"data": [{"id": "1"}, {"id": "2"}], "meta": {"page": 1}}, [{"id": "1"}, {"id": "2"}]),
        ({"id": "1"}, [{"id": "1"}]),
        ({"data": None}, []),
    ],
)
def test_the_data_envelope_is_transport_not_resource(body: Any, expected: Any) -> None:
    assert bs.unwrap(body) == expected


def test_an_unquoted_placeholder_body_is_repaired_not_dropped() -> None:
    assert bs.parse_body('{"amount": {{amount}}}') == {"amount": "{{amount}}"}


def test_an_unparseable_body_is_none() -> None:
    assert bs.parse_body("not json at all {") is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("{{apiRoute}}/account/{{firmId}}", "account"),
        ("{{pfolioApiRoute}}/portfoliogroup/{{firmId}}", "portfoliogroup"),
        ("{{apiRoute}}/v1/webhook", "webhook"),
        ("{{apiRoute}}/portfoliotransaction/{{firmId}}?linkId={{linkId}}", "portfoliotransaction"),
        ("https://{{environment}}-manual-transfers", None),
    ],
)
def test_resource_is_the_segment_after_whichever_base_variable(
    url: str, expected: str | None
) -> None:
    assert bs.resource_of({"request": {"url": {"raw": url}}}) == expected


def test_placeholder_holes_are_located_for_the_validation_gate() -> None:
    payload = {
        "id": "1",
        "date": "{{currentdate}}",
        "nested": {"x": "{{x}}", "y": 2},
        "rows": [{"a": "{{a}}"}],
    }
    assert bs.placeholder_paths(payload) == {("date",), ("nested", "x"), ("rows", 0, "a")}


def test_every_modelled_resource_is_spelt_out_in_pascal_case() -> None:
    assert set(bs.RESOURCES) == set(bs.CLASS_NAMES)
    for resource, stem in bs.CLASS_NAMES.items():
        assert stem[0].isupper(), stem
        assert stem.lower().replace("file", "files") or resource  # names stay related


def test_checked_in_schemas_match_a_fresh_build() -> None:
    """`moon run seccl:build-schemas` output is reviewed, so it must not drift from the
    vendored collection."""
    fresh = bs.build()
    for resource, schema in fresh.items():
        path = bs.SCHEMA_DIR / f"{resource}.json"
        assert path.exists(), f"{resource}: run scripts/build_schemas.py"
        assert json.loads(path.read_text()) == schema, f"{resource} is stale"
    assert sorted(fresh) == sorted(p.stem for p in bs.SCHEMA_DIR.glob("*.json"))
