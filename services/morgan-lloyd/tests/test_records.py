"""Dynamics record normalisation: key sanitising, annotation dropping, widening."""

from __future__ import annotations

import json

import pyarrow as pa

from ml_sync.records import normalise_record, previous_table, records_to_table, sanitise_key


def test_sanitise_key_handles_dynamics_shapes() -> None:
    assert sanitise_key("@odata.etag") == "odata_etag"
    assert sanitise_key("_cam_scheme_value") == "cam_scheme_value"
    assert sanitise_key("cam_nominationofbeneficiaryid") == "cam_nominationofbeneficiaryid"
    assert (
        sanitise_key("DateofInvestment_x002e_ft_transactiondate")
        == "dateof_investment__ft_transactiondate"
    )
    assert sanitise_key("schemeMemberEventId") == "scheme_member_event_id"


def test_normalise_drops_annotations_keeps_etag() -> None:
    record = {
        "@odata.type": "#Microsoft.Dynamics.CRM.contact",
        "@odata.etag": 'W/"417186390"',
        "incomeAmount@odata.type": "#Decimal",
        "incomeAmount": 100.5,
        "contactid": "g-1",
    }
    out = normalise_record(record)
    assert out == {"odata_etag": 'W/"417186390"', "income_amount": 100.5, "contactid": "g-1"}


def test_normalise_serialises_nested_structures() -> None:
    out = normalise_record({"schemeMemberId": {"as_schememember": "g-2"}})
    assert json.loads(out["scheme_member_id"]) == {"as_schememember": "g-2"}


def test_records_to_table_widens_mixed_types_to_string() -> None:
    table = records_to_table(
        [{"cam_retirementageint": 65}, {"cam_retirementageint": "65"}]  # seen in the docs
    )
    assert table.column("cam_retirementageint").type == pa.string()


def test_records_to_table_empty_uses_spine() -> None:
    table = records_to_table([], spine={"contactid": pa.string()})
    assert table.num_rows == 0
    assert table.column_names == ["contactid"]


def test_spine_columns_always_present_and_typed() -> None:
    """The valuation ledger's `value` column is all-NULL while pushes are
    stubbed — spine columns must exist typed, not vanish as Arrow nulls."""
    import pyarrow as pa

    table = records_to_table(
        [{"schemeReference": "MLSIPP/1"}, {"schemeReference": "MLSIPP/2"}],
        spine={"scheme_reference": pa.string(), "value": pa.float64()},
    )
    assert table.column_names[:2] == ["scheme_reference", "value"]
    assert table.column("value").type == pa.float64()
    assert table.column("value").to_pylist() == [None, None]


def _beneficiary_columns() -> tuple[str, ...]:
    """The columns an empty beneficiaries pull publishes.

    The suite loads the interlace project first. That import is then dropped from
    ``sys.modules``, so importing ``raw_core`` again registers ``raw.contacts`` twice.
    The registered function still closes over the column tuple.
    """
    import sys
    from pathlib import Path

    from interlace.dsl.decorators import REGISTRY

    definition = REGISTRY.models.get("raw.beneficiaries")
    if definition is None or definition.fn is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "models"))
        import raw_core

        assert any(item.name == "raw.beneficiaries" for item in raw_core.LIST_ENTITIES)
        definition = REGISTRY.models["raw.beneficiaries"]
    fn = definition.fn
    assert fn is not None and fn.__closure__ is not None
    cells = dict(zip(fn.__code__.co_freevars, fn.__closure__, strict=True))
    return tuple(cells["columns"].cell_contents)


def test_an_empty_beneficiary_listing_still_binds() -> None:
    """The platform model orders by modifiedon. An empty pull must still have that column."""
    import duckdb

    spine = {name: pa.string() for name in _beneficiary_columns()}
    table = records_to_table([], spine=spine)
    connection = duckdb.connect()
    connection.register("beneficiaries", table)
    rows = connection.execute(
        """
        WITH current AS (
            SELECT * FROM beneficiaries
            QUALIFY row_number() OVER (
                PARTITION BY cam_nominationofbeneficiaryid
                ORDER BY modifiedon DESC NULLS LAST
            ) = 1
        )
        SELECT
            cam_nominationofbeneficiaryid,
            try_cast(modifiedon AS timestamp),
            cam_name,
            cam_nameofbeneficiary,
            cam_firstname,
            cam_surname,
            try_cast(cam_dateofbirth AS date),
            statecode,
            try_cast(createdon AS timestamp)
        FROM current
        """
    ).fetchall()
    assert rows == []


def test_previous_table_treats_a_gc_d_snapshot_as_missing() -> None:
    class Gone:
        def table(self) -> None:
            raise RuntimeError("Binder Error: not found")

    class Present:
        def table(self) -> pa.Table:
            return pa.table({"id": ["1"]})

    assert previous_table(None) is None
    assert previous_table(Gone()) is None
    assert previous_table(Present()).num_rows == 1
