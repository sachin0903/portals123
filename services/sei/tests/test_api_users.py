"""``ref.employee_users`` — the empty-read refusals.

The load-bearing behaviour is not the happy path. ``full_merge`` still deletes vanished
keys, and this relation is the only place an SEI employee's email exists. A failed read
must not look like "SEI has no employees" when a previous table is still there.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_spec = importlib.util.spec_from_file_location(
    "api_users", Path(__file__).resolve().parents[1] / "models" / "api_users.py"
)
assert _spec and _spec.loader
users = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(users)

NOW = datetime(2026, 9, 22, 0, 40, tzinfo=UTC)


class TestConform:
    def test_a_row_with_an_email_lands(self) -> None:
        table = users.conform(
            [
                {
                    "uniqueUserId": 58254750,
                    "firmId": 3102,
                    "emailId": "user@example.com",
                    "firstName": "Ada",
                    "lastName": "Lovelace",
                    "loginUserName": "alovelace",
                }
            ],
            as_of=NOW.date(),
            fetched_at=NOW,
        )
        [row] = table.to_pylist()
        assert row["entity_id"] == "58254750"
        assert row["firm_id"] == "3102"
        assert row["email"] == "user@example.com"
        assert row["first_name"] == "Ada"

    def test_a_row_with_no_unique_user_id_is_dropped(self) -> None:
        table = users.conform(
            [{"firmId": 3102, "emailId": "user@example.com"}],
            as_of=NOW.date(),
            fetched_at=NOW,
        )
        assert table.num_rows == 0


class TestRefusals:
    async def test_no_api_base_url_disables_rather_than_failing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(users, "SeiSettings", lambda: _Settings(api_base_url=""))
        table = await users.ref_employee_users()
        assert table.num_rows == 0

    async def test_every_page_failing_refuses_to_delete_all_rows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The unscoped GET 400s `processingOrgId`. That must not empty the relation."""

        async def fetch(_settings: Any, **_kwargs: Any) -> Any:
            stats = type("Stats", (), {"failures": {"page/1": "HTTP 400"}})()
            return [], stats

        monkeypatch.setattr(users, "fetch_employee_users", fetch)
        monkeypatch.setattr(
            users,
            "SeiSettings",
            lambda: _Settings(api_base_url="https://x"),
        )
        with pytest.raises(RuntimeError, match="refusing to delete all rows"):
            await users.ref_employee_users(_Existing(["58254750"]))

    async def test_a_missing_previous_table_does_not_block_a_rebuild(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After GC the snapshot name is gone. That is not existing employees to protect."""

        async def fetch(_settings: Any, **_kwargs: Any) -> Any:
            stats = type("Stats", (), {"failures": {"page/1": "HTTP 500"}})()
            return [], stats

        monkeypatch.setattr(users, "fetch_employee_users", fetch)
        monkeypatch.setattr(
            users,
            "SeiSettings",
            lambda: _Settings(api_base_url="https://x"),
        )
        table = await users.ref_employee_users(_Gone())
        assert table.num_rows == 0


class _Existing:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    def table(self) -> Any:
        import pyarrow as pa

        return pa.table({"entity_id": self._ids})


class _Gone:
    def table(self) -> Any:
        raise RuntimeError("Binder Error: Table with name employee_users does not exist")


class _Settings:
    def __init__(self, *, api_base_url: str) -> None:
        self.api_base_url = api_base_url
        self.service_id = "1"
