"""Which client documents get copied into a tenant bucket, and which wait."""

from __future__ import annotations

from typing import Any

from seccl_sync.publish import (
    Assignment,
    ClientHome,
    LandedFile,
    already_filed,
    assignments,
    canonical_created,
    classify,
    created_by_message,
    document_name,
    landed_files,
    publish_all,
    stamp_created,
    tenant_object_key,
)


def _file(**kwargs: Any) -> LandedFile:
    row = LandedFile(
        file_id="F1",
        message_id="M1",
        firm_id="SELCT",
        client_id="C1",
        file_type="contractnote",
        object_key="raw/seccl/files/SELCT/contractnote/C1/F1-note.pdf",
        filename="note.pdf",
        size_bytes=12,
        sha256="abc",
    )
    if not kwargs:
        return row
    return LandedFile(**{**row.__dict__, **kwargs})


def _home(**kwargs: Any) -> ClientHome:
    row = ClientHome(tenant_id=7, contact_id=3, bucket="t-abc")
    if not kwargs:
        return row
    return ClientHome(**{**row.__dict__, **kwargs})


def test_tenant_key_is_stable_and_collision_proof() -> None:
    key = tenant_object_key(
        file_type="contractnote", client_id="C1", file_id="F1", filename="note.pdf"
    )
    assert key == "files/provider/seccl/contractnote/C1/F1-note.pdf"
    other = tenant_object_key(
        file_type="contractnote", client_id="C1", file_id="F2", filename="note.pdf"
    )
    assert other != key


def test_tenant_key_uses_seccls_filename_when_that_is_already_the_file_id() -> None:
    retirement = tenant_object_key(
        file_type="retirementpack",
        client_id="02HCG85",
        file_id="6463a22ab81a23cfd1afda51-1730619030349",
        filename="6463a22ab81a23cfd1afda51-1730619030349.pdf",
    )
    note = tenant_object_key(
        file_type="contractnote",
        client_id="03235JB",
        file_id="0000FLMHS",
        filename="0000FLMHS.pdf",
    )
    statement = tenant_object_key(
        file_type="bcestatement",
        client_id="03255HB",
        file_id="BCEStatement-1721744514588",
        filename="BCEStatement-1721744514588.pdf",
    )
    assert retirement.endswith("/6463a22ab81a23cfd1afda51-1730619030349.pdf")
    assert note.endswith("/0000FLMHS.pdf")
    assert statement.endswith("/BCEStatement-1721744514588.pdf")
    assert "-6463a22ab81a23cfd1afda51-1730619030349-6463" not in retirement
    assert "0000FLMHS-0000FLMHS" not in note


def test_tenant_key_strips_path_characters() -> None:
    key = tenant_object_key(
        file_type="contractnote",
        client_id="../C1",
        file_id="F 1",
        filename="../../note.pdf",
    )
    assert ".." not in key
    assert key.endswith("/F-1-note.pdf")


def test_classify_maps_known_types_and_keeps_the_slug_legal() -> None:
    assert classify("taxpack") == ("tax", "Tax")
    assert classify("contractnote") == ("contract_note", "Contract note")
    slug, _label = classify("something-new")
    assert slug.replace("_", "").isalnum()


def test_document_name_prefers_the_filename() -> None:
    assert document_name("Q1 statement.pdf", "Statement") == "Q1 statement"
    assert document_name("1660640202083.pdf", "Statement") == "Statement"


def test_landed_files_drops_a_row_with_nothing_to_copy() -> None:
    columns = {
        "id": ["F1", "F2", None],
        "client_id": ["C1", "", "C3"],
        "object_key": ["k1", "k2", "k3"],
        "sha256": ["h1", "h2", "h3"],
        "file_type": ["contractnote", "contractnote", "contractnote"],
    }
    assert [item.file_id for item in landed_files(columns)] == ["F1"]


def test_assignments_skip_filed_pairs_and_clients_with_no_home() -> None:
    files = [_file(), _file(file_id="F2", client_id="C2"), _file(file_id="F3", client_id="C9")]
    homes = {
        "C1": [_home()],
        "C2": [_home(), _home(tenant_id=8, contact_id=4, bucket="t-def")],
    }
    filed_key = tenant_object_key(
        file_type="contractnote", client_id="C1", file_id="F1", filename="note.pdf"
    )
    pending = assignments(files, homes, {("F1", 7): (filed_key, None)})
    assert [(item.file.file_id, item.home.tenant_id) for item in pending] == [("F2", 7), ("F2", 8)]


def test_a_doubled_name_is_copied_again_under_the_single_name() -> None:
    item = _file(
        file_id="0000FLMHS",
        client_id="03235JB",
        filename="0000FLMHS.pdf",
        file_type="contractnote",
    )
    doubled = "files/provider/seccl/contractnote/03235JB/0000FLMHS-0000FLMHS.pdf"
    [pending] = assignments([item], {"03235JB": [_home()]}, {("0000FLMHS", 7): (doubled, None)})
    assert pending.storage_key.endswith("/0000FLMHS.pdf")
    assert pending.previous_key == doubled


def test_already_filed_reads_the_ledger_pair() -> None:
    assert already_filed({"id": ["F1", None], "tenant_id": [7, 8]}) == {("F1", 7): ("", None)}
    stamped = "2024-11-03T08:50:30.349000+00:00"
    assert already_filed(
        {
            "id": ["F1"],
            "tenant_id": [7],
            "storage_key": ["files/a.pdf"],
            "created": ["2024-11-03T08:50:30.349Z"],
        }
    ) == {("F1", 7): ("files/a.pdf", stamped)}


def test_message_created_is_one_utc_spelling() -> None:
    assert canonical_created("2024-11-03T08:50:30.349Z") == "2024-11-03T08:50:30.349000+00:00"
    assert canonical_created("not a date") is None
    by_message = created_by_message(
        {"id": ["M1", "M2"], "created": ["2024-11-03T08:50:30.349Z", None]}
    )
    assert by_message == {"M1": "2024-11-03T08:50:30.349000+00:00"}
    [stamped] = stamp_created([_file(message_id="M1")], by_message)
    assert stamped.created == "2024-11-03T08:50:30.349000+00:00"


def test_a_copy_filed_without_a_date_is_filed_again_for_the_message_time() -> None:
    item = _file(created="2024-11-03T08:50:30.349000+00:00")
    key = tenant_object_key(
        file_type="contractnote", client_id="C1", file_id="F1", filename="note.pdf"
    )
    [pending] = assignments([item], {"C1": [_home()]}, {("F1", 7): (key, None)})
    assert pending.storage_key == key
    assert pending.previous_key == key
    assert assignments([item], {"C1": [_home()]}, {("F1", 7): (key, item.created)}) == []


class _Copier:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.copied: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    async def copy(self, source_key: str, dest_bucket: str, dest_key: str) -> None:
        if source_key in self.fail:
            raise RuntimeError("no")
        self.copied.append((source_key, dest_bucket, dest_key))

    async def delete(self, bucket: str, key: str) -> None:
        self.deleted.append((bucket, key))


class _Book:
    def __init__(self) -> None:
        self.filed: list[str] = []

    def homes(self, client_ids: list[str]) -> dict[str, list[ClientHome]]:
        return {}

    def ingest(self, item: Assignment) -> str:
        self.filed.append(item.file.file_id)
        return f"uid-{item.file.file_id}"


async def test_one_bad_copy_does_not_strand_the_rest() -> None:
    good = _file(object_key="ok")
    bad = _file(file_id="F2", object_key="bad")
    pending = assignments([good, bad], {"C1": [_home()]}, {})
    book = _Book()
    filed = await publish_all(pending, copier=_Copier({"bad"}), book=book, concurrency=2)
    assert [item.file_id for item in filed] == ["F1"]
    assert book.filed == ["F1"]


async def test_replacing_a_doubled_name_removes_the_old_object() -> None:
    item = _file(file_id="0000FLMHS", client_id="03235JB", filename="0000FLMHS.pdf")
    doubled = "files/provider/seccl/contractnote/03235JB/0000FLMHS-0000FLMHS.pdf"
    pending = assignments(
        [item], {"03235JB": [_home(bucket="t-abc")]}, {("0000FLMHS", 7): (doubled, None)}
    )
    copier = _Copier()
    filed = await publish_all(pending, copier=copier, book=_Book(), concurrency=1)
    assert filed[0].storage_key.endswith("/0000FLMHS.pdf")
    assert copier.deleted == [("t-abc", doubled)]


async def test_a_date_correction_does_not_recopy_the_object() -> None:
    created = "2024-11-03T08:50:30.349000+00:00"
    item = _file(created=created)
    key = tenant_object_key(
        file_type="contractnote", client_id="C1", file_id="F1", filename="note.pdf"
    )
    pending = assignments([item], {"C1": [_home()]}, {("F1", 7): (key, None)})
    copier = _Copier()
    filed = await publish_all(pending, copier=copier, book=_Book(), concurrency=1)
    assert copier.copied == []
    assert copier.deleted == []
    assert filed[0].created == created
    assert filed[0].storage_key == key
