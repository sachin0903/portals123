"""Document downloads: which messages carry a file, and the concurrent fan-out."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from seccl_sync.files import (
    MESSAGE_TYPES,
    ObjectStore,
    PendingFile,
    fetch_all,
    object_key,
    pending,
)
from seccl_sync.settings import ObjectStoreSettings


def _messages(*rows: dict[str, Any]) -> dict[str, list[Any]]:
    """Column-major view of landed messages, as the model passes it in."""
    keys = ("id", "message_type", "client_id", "firm_id", "payload", "status")
    return {key: [row.get(key) for row in rows] for key in keys}


def _row(**kwargs: Any) -> dict[str, Any]:
    row = {
        "id": "M1",
        "message_type": "CONTRACT_NOTE",
        "client_id": "C1",
        "firm_id": "SELCT",
        "payload": json.dumps({"id": "F1"}),
        "status": "ACTIVE",
    }
    row.update(kwargs)
    return row


def test_pending_reads_the_file_id_out_of_the_json_payload() -> None:
    [item] = pending(_messages(_row()), set())
    assert (item.file_id, item.message_id, item.path_type) == ("F1", "M1", "contractnote")


def test_pending_skips_already_downloaded_inactive_and_untyped() -> None:
    rows = _messages(
        _row(id="M1", payload=json.dumps({"id": "HAVE"})),
        _row(id="M2", status="CANCELLED", payload=json.dumps({"id": "F2"})),
        _row(id="M3", message_type="SOMETHING_ELSE", payload=json.dumps({"id": "F3"})),
        _row(id="M4", payload=json.dumps({"noFileHere": True})),
        _row(id="M5", payload=None),
        _row(id="M6", payload="not json"),
        _row(id="M7", payload=json.dumps({"id": "F7"})),
    )
    assert [item.file_id for item in pending(rows, {"HAVE"})] == ["F7"]


def test_pending_deduplicates_a_file_referenced_twice() -> None:
    rows = _messages(_row(id="M1"), _row(id="M2"))  # same payload id
    assert len(pending(rows, set())) == 1


def test_every_message_type_maps_to_a_lowercase_path_segment() -> None:
    assert all(segment.islower() and segment.isalpha() for segment in MESSAGE_TYPES.values())


def test_object_key_is_namespaced_and_collision_proof() -> None:
    settings = ObjectStoreSettings(prefix="raw/seccl/files")
    item = PendingFile("M1", "CONTRACT_NOTE", "contractnote", "F1", "SELCT", "C1")
    assert object_key(settings, item, "note.pdf") == (
        "raw/seccl/files/SELCT/contractnote/C1/F1-note.pdf"
    )
    # same filename, different file id => different key
    other = PendingFile("M2", "CONTRACT_NOTE", "contractnote", "F2", "SELCT", "C1")
    assert object_key(settings, other, "note.pdf") != object_key(settings, item, "note.pdf")


class _FakeStore(ObjectStore):
    def __init__(self) -> None:
        super().__init__(ObjectStoreSettings(bucket="core", prefix="raw/seccl/files"))
        self.put_keys: list[str] = []

    async def put(self, key: str, body: bytes, content_type: str | None = None) -> None:
        self.put_keys.append(key)


class _FakeApi:
    """Records overlap so the test can prove the fan-out is concurrent, not serial."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.in_flight = 0
        self.max_in_flight = 0

    async def file_url(self, message_type: str, body: dict[str, Any]) -> str:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.02)
            if body["id"] in self.fail:
                raise RuntimeError("no url for you")
            return f"https://files.example/{body['id']}/doc.pdf?sig=x"
        finally:
            self.in_flight -= 1

    async def download(self, url: str) -> bytes:
        await asyncio.sleep(0.01)
        return b"%PDF-1.7 fake"


def _items(n: int) -> list[PendingFile]:
    return [
        PendingFile(f"M{i}", "CONTRACT_NOTE", "contractnote", f"F{i}", "SELCT", f"C{i}")
        for i in range(n)
    ]


async def test_fetch_all_runs_concurrently_and_returns_metadata() -> None:
    api, store = _FakeApi(), _FakeStore()
    records = await fetch_all(_items(10), api=api, store=store)  # type: ignore[arg-type]

    assert len(records) == 10
    assert api.max_in_flight > 1, "downloads must overlap, not run one at a time"
    assert len(store.put_keys) == 10
    first = records[0]
    assert first["bucket"] == "core"
    assert first["objectKey"].startswith("raw/seccl/files/SELCT/contractnote/")
    assert first["bytes"] == len(b"%PDF-1.7 fake")
    assert len(first["sha256"]) == 64
    assert first["filename"] == "doc.pdf"


async def test_one_bad_document_does_not_strand_the_rest() -> None:
    api, store = _FakeApi(fail={"F3"}), _FakeStore()
    records = await fetch_all(_items(5), api=api, store=store)  # type: ignore[arg-type]
    assert sorted(record["id"] for record in records) == ["F0", "F1", "F2", "F4"]


@pytest.mark.parametrize("use_ssl,expected", [(False, "http://h:9000"), (True, "https://h:9000")])
def test_endpoint_url_follows_use_ssl(use_ssl: bool, expected: str) -> None:
    assert ObjectStoreSettings(endpoint="h:9000", use_ssl=use_ssl).endpoint_url == expected
