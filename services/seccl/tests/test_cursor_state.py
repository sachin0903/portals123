"""A rebuild must continue from the stored start date, not list the endpoint in full."""

from datetime import date

import pyarrow as pa
import pytest

from seccl_sync.cursor_state import keep, prefer_fresh, resume


def test_an_injected_cursor_is_the_start_and_does_not_need_stored_rows() -> None:
    start, existing = resume(date(2026, 10, 1), "notifications", "created")
    assert start == date(2026, 10, 1)
    assert existing is None


def test_a_rebuilt_batch_keeps_stored_rows_and_prefers_the_new_copy() -> None:
    existing = pa.table(
        {"id": ["a", "b"], "created": ["2026-09-01", "2026-09-02"], "note": ["old", "old"]}
    )
    fresh = pa.table({"id": ["b"], "created": ["2026-10-02"], "note": ["new"]})
    out = prefer_fresh(existing, fresh, "id")
    assert out is not None
    got = {row["id"]: row for row in out.to_pylist()}
    assert set(got) == {"a", "b"}
    assert got["a"]["note"] == "old"
    assert got["b"]["note"] == "new"
    assert got["b"]["created"] == "2026-10-02"


def test_a_rebuild_widens_whole_numbers_onto_stored_doubles() -> None:
    existing = pa.table(
        {
            "id": ["a", "b"],
            "consideration": pa.array([1.5, 2.25], pa.float64()),
            "auto_set_book_value": [True, False],
        }
    )
    fresh = pa.table({"id": ["b"], "consideration": pa.array([3], pa.int64())})
    out = prefer_fresh(existing, fresh, "id")
    assert out is not None
    assert out.schema.field("consideration").type == pa.float64()
    got = {row["id"]: row for row in out.to_pylist()}
    assert set(got) == {"a", "b"}
    assert got["a"]["consideration"] == 1.5
    assert got["a"]["auto_set_book_value"] is True
    assert got["b"]["consideration"] == 3.0
    assert got["b"]["auto_set_book_value"] is None


def test_an_ordinary_run_does_not_republish_stored_rows() -> None:
    existing = pa.table({"id": ["a"], "created": ["2026-09-01"]})
    fresh = pa.table({"id": ["b"], "created": ["2026-10-02"]})
    out = keep(date(2026, 10, 1), existing, fresh, "id")
    assert out.column("id").to_pylist() == ["b"]


def test_a_rebuild_without_a_key_refuses_to_replace_the_stored_rows() -> None:
    existing = pa.table({"created": ["2026-09-01"]})
    fresh = pa.table({"id": ["b"], "created": ["2026-10-02"]})
    with pytest.raises(RuntimeError, match="not replacing"):
        keep(None, existing, fresh, "id")
