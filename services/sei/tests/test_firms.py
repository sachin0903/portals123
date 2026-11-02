"""The API firm list is the SFTP Reference report."""

from __future__ import annotations

import pyarrow as pa

from sei_sync.firms import reference_firm_ids, resolve_firm_ids


class _Handle:
    def __init__(self, table: pa.Table) -> None:
        self._table = table

    def table(self) -> pa.Table:
        return self._table


def test_the_reference_report_is_the_firm_list() -> None:
    handle = _Handle(pa.table({"firm_id": ["3118", "3116", "3102"], "firm_name": ["V", "C", "B"]}))
    assert resolve_firm_ids(handle) == ["3102", "3116", "3118"]


def test_an_unreadable_report_requests_nobody() -> None:
    assert resolve_firm_ids(None) == []


def test_a_readable_report_with_no_rows_requests_nobody() -> None:
    handle = _Handle(pa.table({"firm_id": pa.array([], type=pa.string())}))
    assert reference_firm_ids(handle) == set()
    assert resolve_firm_ids(handle) == []
