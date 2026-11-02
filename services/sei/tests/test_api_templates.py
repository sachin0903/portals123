"""``ref.enrollment_templates`` — the conform step and the two refusals.

The load-bearing behaviour is not the happy path. It is that an unreadable or unconfigured
firm produces an EMPTY table with a warning rather than an exception, and that a row SEI sends
under a different key still lands — because both alternatives look the same from the outside
("this firm has no templates") and only one of them is true.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

_spec = importlib.util.spec_from_file_location(
    "api_templates", Path(__file__).resolve().parents[1] / "models" / "api_templates.py"
)
assert _spec and _spec.loader
templates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(templates)

NOW = datetime(2026, 8, 27, 7, 30, tzinfo=UTC)


class TestConform:
    def test_the_screaming_snake_shape_lands(self) -> None:
        """The shape SEI's static-reference workbook uses."""
        table = templates.conform(
            [
                {
                    "FIRM_ID": 3102,
                    "TEMPLATE_ID": 2981049,
                    "TEMPLATE_NAME": "Blackfinch Client",
                    "TEMPLATE_TYPE": 1,
                    "TEMPLATE_TYPE_DESC": "Client",
                    "_firm_id": "3102",
                    "_scope": "client",
                }
            ],
            as_of=NOW.date(),
            fetched_at=NOW,
        )
        [row] = table.to_pylist()
        assert row["firm_id"] == "3102"
        assert row["template_id"] == "2981049"
        assert row["template_name"] == "Blackfinch Client"
        assert row["scope"] == "client"

    def test_the_camel_case_shape_lands_too(self) -> None:
        """The two template services answer the same information under different names, and
        which one you get depends on the service."""
        table = templates.conform(
            [
                {
                    "templateId": "2981141",
                    "templateName": "Blackfinch AIM ISA - SITest",
                    "templateType": "2",
                    "_firm_id": "3102",
                    "_scope": "account",
                }
            ],
            as_of=NOW.date(),
            fetched_at=NOW,
        )
        [row] = table.to_pylist()
        assert row["template_id"] == "2981141"
        assert row["template_name"] == "Blackfinch AIM ISA - SITest"

    def test_ids_are_strings_whichever_type_sei_sent(self) -> None:
        """SEI sends `TEMPLATE_ID` as a number in one service and a string in the other; a
        column whose type depends on the answering service is unjoinable."""
        table = templates.conform(
            [
                {"TEMPLATE_ID": 2981049, "_firm_id": "3102", "_scope": "client"},
                {"templateId": "2981050", "_firm_id": "3102", "_scope": "account"},
            ],
            as_of=NOW.date(),
            fetched_at=NOW,
        )
        assert table.column("template_id").type == pa.string()
        assert table.column("template_id").to_pylist() == ["2981049", "2981050"]

    def test_a_row_with_no_id_is_dropped(self) -> None:
        """It cannot be used in an enrolment, and keeping it puts a NULL in the column the
        product mapping is keyed on."""
        table = templates.conform(
            [{"TEMPLATE_NAME": "Nameless", "_firm_id": "3102", "_scope": "client"}],
            as_of=NOW.date(),
            fetched_at=NOW,
        )
        assert table.num_rows == 0

    def test_an_empty_read_is_typed(self) -> None:
        """An all-NULL untyped column would be dropped by the warehouse, so the empty table
        still has to carry the schema."""
        table = templates.conform([], as_of=NOW.date(), fetched_at=NOW)
        assert table.num_rows == 0
        assert table.column_names == list(templates.SCHEMA)
        assert table.column("template_id").type == pa.string()


class TestRefusals:
    async def test_no_api_base_url_disables_rather_than_failing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deployment with SFTP credentials and no API credentials has to keep running."""
        monkeypatch.setattr(templates, "SeiSettings", lambda: _Settings(api_base_url=""))
        table = await templates.ref_enrollment_templates()
        assert table.num_rows == 0

    async def test_no_firm_ids_does_not_fan_out_to_the_whole_delivery_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The drop is whole-group, so defaulting to "every firm in the drop" would issue a
        request per firm in someone else's book."""
        called: list[Any] = []

        async def fetch(settings: Any, firm_ids: list[str]) -> Any:
            called.append(firm_ids)
            raise AssertionError("should not have been called")

        monkeypatch.setattr(templates, "fetch_templates", fetch)
        monkeypatch.setattr(templates, "SeiSettings", lambda: _Settings(api_base_url="https://x"))
        table = await templates.ref_enrollment_templates()
        assert table.num_rows == 0
        assert called == []

    async def test_a_failed_firm_does_not_lose_the_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fetch(settings: Any, firm_ids: list[str]) -> Any:
            stats = type("Stats", (), {"failures": {"3106/client": "HTTP 403"}})()
            return (
                [{"TEMPLATE_ID": 1, "_firm_id": "3102", "_scope": "client"}],
                stats,
            )

        monkeypatch.setattr(templates, "fetch_templates", fetch)
        monkeypatch.setattr(
            templates,
            "SeiSettings",
            lambda: _Settings(api_base_url="https://x"),
        )
        table = await templates.ref_enrollment_templates(_Firms(["3102", "3106"]))
        assert table.num_rows == 1

    async def test_the_reference_report_names_the_firms(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[list[str]] = []

        async def fetch(settings: Any, firm_ids: list[str]) -> Any:
            called.append(firm_ids)
            return [], type("Stats", (), {"failures": {}})()

        monkeypatch.setattr(templates, "fetch_templates", fetch)
        monkeypatch.setattr(
            templates,
            "SeiSettings",
            lambda: _Settings(api_base_url="https://x"),
        )
        await templates.ref_enrollment_templates(_Firms(["3116", "3118"]))
        assert called == [["3116", "3118"]]


class _Settings:
    def __init__(self, *, api_base_url: str) -> None:
        self.api_base_url = api_base_url


class _Firms:
    def __init__(self, firm_ids: list[str]) -> None:
        import pyarrow as pa

        self._table = pa.table({"firm_id": firm_ids})

    def table(self) -> object:
        return self._table
