"""Morgan Lloyd publishes what the book pipeline reads — for the entities it is registered for.

ML is the one provider `book_sink` registers TODAY beyond Seccl (`clients`), so unlike SEI's
standing gap table this is a hard requirement rather than a record of one: a column the sink reads
and this model does not publish is a DuckDB Binder Error that aborts the tenant, and the ledger
records it as "unavailable" — indistinguishable from "this firm has no data with this provider".

Which is exactly how it was when the check was written: the model published the address as four flat
columns where §3 (and Seccl, and SEI) publish one struct, and `full_name` where the sink reads
`entity_name`. Six columns short, and nothing would have said so until an ML tenant's slice went
quietly missing.

Read with sqlglot rather than by executing the model: ML's `raw.contacts` is 457 columns landed off
the live OData entity, so a stub warehouse for it would be a fixture nobody could keep true.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import sqlglot
from book_sink import CONFORMED, required_columns
from book_sink.entities import PROVIDER_ENTITIES

MODELS = Path(__file__).resolve().parent.parent / "models" / "platform"
PROVIDER = "morgan-lloyd"


def _published(model: str) -> set[str]:
    sql = re.sub(r"/\*.*?\*/", "", (MODELS / f"{model}.sql").read_text(), count=1, flags=re.S)
    return set(sqlglot.parse_one(sql.strip(), dialect="duckdb").named_selects)


def _registered() -> list[tuple[str, frozenset[str]]]:
    """Per registered relation, the UNION of what every sink entity reading it needs. Union, because
    two entities can read one relation and taking either alone understates the contract."""
    published = PROVIDER_ENTITIES.get(PROVIDER, ())
    needs: dict[str, set[str]] = {}
    for entity in CONFORMED:
        if entity.source in published:
            needs.setdefault(entity.source, set()).update(required_columns(entity))
    return [(model, frozenset(columns)) for model, columns in sorted(needs.items())]


def test_something_is_registered() -> None:
    """If ML is un-registered this file is vacuous, and a vacuous green test is worse than none."""
    assert _registered(), f"book_sink registers no entities for {PROVIDER}"


def test_being_registered_means_being_wired() -> None:
    """`PROVIDER_ENTITIES` saying ML merges `clients` is a claim about this project, and for a while
    it was false: there was no `models/tenants.py` and no `attach: book:`, so the sink registered
    nothing, no staging model existed, and the whole per-tenant chain was simply absent. Nothing
    failed — an absent model is not an error, and `--select tag:tenant:{uid}` on a project with no
    such models selects the empty set and reports success.

    One level up from the rest of this file: those tests ask whether the columns line up, this one
    asks whether anything reads them.
    """
    project = MODELS.parent.parent
    declaration = project / "models" / "tenants.py"
    assert declaration.exists(), (
        f"book_sink registers {[m for m, _ in _registered()]} for {PROVIDER}, but there is no "
        f"models/tenants.py — the per-tenant models are never registered and the chain is absent"
    )
    assert "register" in declaration.read_text()
    config = (project / "interlace.yaml").read_text()
    assert "book:" in config, (
        "interlace.yaml declares no `book:` attachment, so a staging model targeting "
        "`book.inbound.…` cannot resolve its destination"
    )


@pytest.mark.parametrize(
    "model,needed", _registered(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_the_model_publishes_everything_the_sink_reads(model: str, needed: frozenset[str]) -> None:
    missing = sorted(needed - _published(model))
    assert not missing, (
        f"platform.{model} does not publish {missing}, which book_sink's projection reads — the "
        f"tenant's slice would abort as a Binder Error and be recorded as 'unavailable'"
    )


def test_the_address_is_one_struct_and_not_four_columns() -> None:
    """The shape §3 fixes, and the specific thing this model got wrong: the book stores one jsonb
    address, so a provider publishing `address_city`/`address_postcode` publishes something no
    reader asks for while the column every reader asks for is absent."""
    published = _published("clients")
    assert "address" in published
    # The flat columns stay published beside it — lake-side readers already select them, and
    # dropping them would be a second change riding along with this one.
    assert {"address_lines", "address_city", "address_postcode"} <= published


def test_the_fields_this_org_has_but_does_not_publish_stay_explicit() -> None:
    """`title`, `nationality` and `national_identifier` exist on this org's contact as the `cam_*`
    fields the create path already sends — they are NULL here because reading them back needs the
    reverse of an option-set map and has not been verified against the nightly pull. Published as
    explicit NULLs so the conformed column set stays fixed, which is what lets a reader not care
    which provider carries which field."""
    published = _published("clients")
    assert {"title", "nationality", "national_identifier", "national_identifier_type"} <= published
