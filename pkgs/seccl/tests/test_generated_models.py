"""Gates on the generated models: they match the schemas, and they accept Seccl's own examples.

The second one is the useful test. These types are inferred, so the only evidence they are
right is that every payload Seccl publishes parses -- 488 of them. It is what caught
`paymentDate` typed as a date (rejecting the timestamps Seccl sends) and
`declarationAcceptedDate` typed as an aware datetime (rejecting the naive half).
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_schemas as bs  # noqa: E402
import generate_models as gen  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def harvested() -> dict[str, bs.Samples]:
    return bs.harvest(json.loads(bs.COLLECTION.read_text()))


def shape_class(resource: str, suffix: str) -> type[BaseModel] | None:
    module = importlib.import_module(f"seccl.models.{resource}")
    return getattr(module, bs.CLASS_NAMES[resource] + suffix, None)


def test_every_modelled_resource_has_a_module() -> None:
    for resource in bs.RESOURCES:
        assert (ROOT / "src" / "seccl" / "models" / f"{resource}.py").exists(), resource


@pytest.mark.parametrize("resource", bs.RESOURCES)
def test_every_shape_with_examples_has_a_class(
    resource: str, harvested: dict[str, bs.Samples]
) -> None:
    samples = harvested[resource]
    for shape, suffix in (("read", ""), ("create", "Create"), ("update", "Update")):
        if samples.of(shape):  # type: ignore[arg-type]
            assert shape_class(resource, suffix) is not None, f"{resource}.{shape}"


@pytest.mark.parametrize("resource", bs.RESOURCES)
def test_seccls_own_examples_validate(resource: str, harvested: dict[str, bs.Samples]) -> None:
    """Errors at a Postman placeholder are forgiven: `"paymentDate": "{{currentdate}}"` is a
    hole the caller fills, so it is not evidence the type is wrong (see placeholder_paths).
    Anything else is a defect in the inference."""
    samples = harvested[resource]
    rejected: list[str] = []
    checked = 0
    for shape, suffix in (("read", ""), ("create", "Create"), ("update", "Update")):
        model = shape_class(resource, suffix)
        if model is None:
            continue
        for example in samples.of(shape):  # type: ignore[arg-type]
            checked += 1
            holes = bs.placeholder_paths(example)
            try:
                model.model_validate(example)
            except ValidationError as error:
                for entry in error.errors():
                    if not _at_a_hole(entry["loc"], holes):
                        loc = ".".join(map(str, entry["loc"]))
                        rejected.append(f"{suffix or 'read'}: {loc} {entry['type']}")
    assert not rejected, (
        f"{resource}: {len(rejected)} real rejections of {checked} -> {rejected[:3]}"
    )


def _at_a_hole(loc: tuple[Any, ...], holes: set[tuple[Any, ...]]) -> bool:
    """Union branches add tags to `loc` ("field", "str"), so compare on prefixes."""
    return any(loc[: len(hole)] == hole or hole[: len(loc)] == loc for hole in holes)


def test_wire_names_survive_the_round_trip(harvested: dict[str, bs.Samples]) -> None:
    """Fields are snake_case in Python and camelCase on the wire; `by_alias` is what Seccl
    must receive, so a dump has to give back the keys the example came with."""
    # The richest example, not the first: some saved responses are `{"data": {}}`.
    example = max(harvested["account"].read, key=len)
    model = shape_class("account", "")
    assert model is not None
    dumped = model.model_validate(example).model_dump(by_alias=True, exclude_none=True)
    assert set(dumped) <= set(example), "dump invented keys Seccl never sent"
    assert "firmId" in dumped or "id" in dumped, "camelCase aliases lost on dump"


def test_models_can_also_be_built_by_field_name() -> None:
    """`populate_by_name`, so calling code can use the snake_case names it reads."""
    create = shape_class("portfoliogroup", "Create")
    assert create is not None
    assert create.model_config.get("populate_by_name") is True


def test_only_shapes_are_re_exported() -> None:
    """Nested concepts stay module-local: two resources give the same name to different
    objects (an account's embedded `Client` is not the `client` resource), so exporting
    them all would resolve one of them to the wrong class."""
    import seccl.models as models

    stems = {bs.CLASS_NAMES[r] for r in bs.RESOURCES}
    for name in models.__all__:
        assert any(name in (s, f"{s}Create", f"{s}Update") for s in stems), name
    assert len(models.__all__) == len(set(models.__all__)), "duplicate export"


def test_the_embedded_client_is_not_the_client_resource() -> None:
    """Guards the collision the export rule exists for."""
    from seccl.models import Client as ClientResource
    from seccl.models.account import Client as EmbeddedClient

    assert ClientResource is not EmbeddedClient


def test_no_duplicate_concept_classes() -> None:
    """`WrapperDetail1`/`WrapperDetail2` are the signature of un-merged examples."""
    offenders: list[str] = []
    for module in sorted((ROOT / "src" / "seccl" / "models").glob("*.py")):
        for line in module.read_text().splitlines():
            if line.startswith("class ") and line.split("(")[0].rstrip().rstrip(":")[-1].isdigit():
                offenders.append(f"{module.name}:{line.split('(')[0][6:]}")
    assert not offenders, offenders


def test_generated_modules_fit_the_line_length_limit() -> None:
    for module in sorted((ROOT / "src" / "seccl" / "models").glob("*.py")):
        for number, line in enumerate(module.read_text().splitlines(), 1):
            assert len(line) <= 100, f"{module.name}:{number} is {len(line)} chars"


@pytest.mark.skipif(
    shutil.which("datamodel-codegen") is None,
    reason="datamodel-code-generator is a dev dependency of this package only",
)
def test_checked_in_models_match_a_fresh_generation(tmp_path: Path) -> None:
    """The modules are checked in, so nothing but the generator should ever change them --
    a hand-edit or a stale schema is otherwise invisible."""
    for schema in sorted(bs.SCHEMA_DIR.glob("*.json")):
        target = tmp_path / f"{schema.stem}.py"
        gen.generate(schema, target)
        committed = (ROOT / "src" / "seccl" / "models" / f"{schema.stem}.py").read_text()
        assert target.read_text() == committed, (
            f"{schema.stem}.py is stale: run `moon run seccl:generate-models`"
        )


def test_the_vendored_collection_is_the_one_seccl_published() -> None:
    """Byte-exact provenance: the models are only as good as their source, so the source is
    not edited in place. Re-vendor a new export instead."""
    collection: dict[str, Any] = json.loads(bs.COLLECTION.read_text())
    assert collection["info"]["schema"].startswith(
        "https://schema.getpostman.com/json/collection/v2.1.0"
    )
    assert collection["info"]["name"] == "Seccl API Documentation"
    assert len(bs.iter_requests(collection)) == 377
