"""SEI document listing, and which copies get filed into a tenant bucket."""

from __future__ import annotations

from datetime import date

import pytest

from sei_sync.api import DocumentServiceUnavailable
from sei_sync.documents import (
    FIRM_GENERIC,
    Party,
    bundle,
    classify,
    content_type,
    core_key,
    fetch_documents,
    flatten,
    list_request,
    parties_from_rows,
)
from sei_sync.publish import (
    ClientHome,
    FirmHome,
    LandedDocument,
    assignments,
    tenant_object_key,
)


def _party(party_id: str = "9012438", firm_id: str = "3106") -> Party:
    return Party(party_id=party_id, firm_id=firm_id)


def _list(*groups: tuple[str, list[dict]]) -> dict:
    return {
        "documentList": [{"documentTypeDesc": name, "documents": docs} for name, docs in groups]
    }


def _doc(**kwargs: object) -> dict:
    row = {
        "documentId": "09000205800de66b",
        "displayName": "statement.pdf",
        "accountNum": "MSP000804",
        "asOfDate": "20190402",
    }
    row.update(kwargs)
    return row


def test_list_request_includes_the_firm_pack_and_skips_us_tax() -> None:
    body = list_request("9012438", date(2026, 9, 22))
    assert body["partyId"] == "9012438"
    assert body["endDate"] == "20260922"
    assert body["includeFirmGenericDocs"] is True
    assert body["includeStatements"] is True
    assert body["includeUKTaxDocuments"] is True
    assert body["includeUSTaxDocuments"] is False


def test_firm_pack_is_not_filed_under_the_client_it_was_listed_with() -> None:
    occurrences = flatten(
        _list(
            ("Statements", [_doc()]),
            (
                FIRM_GENERIC,
                [_doc(documentId="firm-1", displayName="factsheet.pdf", accountNum="X")],
            ),
        ),
        _party(),
    )
    bundled = bundle(occurrences)
    by_id = {item.document_id: item for item in bundled}
    assert by_id["09000205800de66b"].firm_level is False
    assert by_id["09000205800de66b"].party_ids == ("9012438",)
    assert by_id["09000205800de66b"].account_numbers == ("MSP000804",)
    firm = by_id["firm-1"]
    assert firm.firm_level is True
    assert firm.party_ids == ()
    assert firm.account_numbers == ()
    assert firm.document_type == "firm_document"
    assert firm.feed == "firm-generic"


def test_the_same_firm_pack_on_two_parties_is_one_document() -> None:
    first = flatten(_list((FIRM_GENERIC, [_doc(documentId="firm-1")])), _party("1"))
    second = flatten(_list((FIRM_GENERIC, [_doc(documentId="firm-1")])), _party("2"))
    bundled = bundle([*first, *second])
    assert len(bundled) == 1
    assert bundled[0].party_ids == ()


def test_a_joint_statement_keeps_both_parties() -> None:
    first = flatten(_list(("Statements", [_doc()])), _party("111"))
    second = flatten(_list(("Statements", [_doc()])), _party("222"))
    bundled = bundle([*first, *second])
    assert len(bundled) == 1
    assert bundled[0].party_ids == ("111", "222")


def test_core_key_is_stable_and_has_no_date() -> None:
    listed = _list(("Statements", [_doc(displayName="Q1 statement.pdf")]))
    item = bundle(flatten(listed, _party()))[0]
    assert core_key(item) == "sei/documents/statements/3106/09000205800de66b-Q1_statement.pdf"
    assert "dt=" not in core_key(item)


def test_classify_unknown_group_is_a_legal_slug() -> None:
    slug, feed, _label, firm = classify("Something New!")
    assert slug == "something_new"
    assert feed == "something-new"
    assert firm is False
    assert classify("other")[0] == "document"


def test_content_type_trusts_the_pdf_magic_over_the_header() -> None:
    assert content_type("x.bin", "application/octet-stream", b"%PDF-1.4") == "application/pdf"


def test_parties_from_rows_honours_the_firm_filter() -> None:
    rows = [
        {"external_id": "1", "firm_id": "3106"},
        {"external_id": "1", "firm_id": "3106"},
        {"external_id": "2", "firm_id": "3102"},
    ]
    assert [party.party_id for party in parties_from_rows(rows, firm_ids={"3106"})] == ["1"]
    assert [party.party_id for party in parties_from_rows(rows, firm_ids=set())] == []
    assert [party.party_id for party in parties_from_rows(rows, firm_ids=None)] == ["1", "2"]


class _Api:
    def __init__(self, lists: dict[str, dict], bodies: dict[str, bytes]) -> None:
        self.lists = lists
        self.bodies = bodies

    async def list_end_client_documents(self, body: dict) -> dict:
        return self.lists[body["partyId"]]

    async def retrieve_document(self, document_id: str) -> tuple[bytes, str | None]:
        return self.bodies[document_id], "application/octet-stream"


class _Store:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self.objects

    def put(self, bucket: str, key: str, body: bytes, *, content_type: str) -> None:
        self.objects[(bucket, key)] = body

    def get(self, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)]


async def test_fetch_downloads_once_and_skips_what_the_warehouse_has() -> None:
    api = _Api(
        {
            "111": _list(
                ("Statements", [_doc(documentId="a")]),
                (FIRM_GENERIC, [_doc(documentId="firm")]),
            )
        },
        {"a": b"%PDF-a", "firm": b"%PDF-f"},
    )
    store = _Store()
    first = await fetch_documents(
        parties=[_party("111")],
        already=set(),
        api=api,
        store=store,
        core_bucket="platforms",
        today=date(2026, 9, 22),
        concurrency=2,
    )
    assert {item.document_id for item in first} == {"a", "firm"}
    assert len(store.objects) == 2
    second = await fetch_documents(
        parties=[_party("111")],
        already={"a", "firm"},
        api=api,
        store=store,
        core_bucket="platforms",
        today=date(2026, 9, 22),
        concurrency=2,
    )
    assert second == []


async def test_an_unentitled_list_fails_the_run() -> None:
    class _Down:
        async def list_end_client_documents(self, body: dict) -> dict:
            raise DocumentServiceUnavailable("proxy")

        async def retrieve_document(self, document_id: str) -> tuple[bytes, str | None]:
            raise AssertionError("must not download")

    with pytest.raises(DocumentServiceUnavailable):
        await fetch_documents(
            parties=[_party()],
            already=set(),
            api=_Down(),
            store=_Store(),
            core_bucket="platforms",
            today=date(2026, 9, 22),
            concurrency=1,
        )


def _landed(**kwargs: object) -> LandedDocument:
    row = LandedDocument(
        document_id="a",
        firm_id="3106",
        firm_level=False,
        document_type="statement",
        type_label="Statement",
        feed="statements",
        display_name="statement.pdf",
        party_ids=("111",),
        account_numbers=("MSP000804",),
        object_key="sei/documents/statements/3106/a-statement.pdf",
        content_type="application/pdf",
        size_bytes=4,
        sha256="abc",
    )
    if not kwargs:
        return row
    return LandedDocument(**{**row.__dict__, **kwargs})


def test_tenant_key_does_not_name_a_party() -> None:
    key = tenant_object_key(_landed())
    assert key == "files/provider/sei/statements/a-statement.pdf"


def test_a_client_document_waits_until_the_party_is_in_a_book() -> None:
    pending = assignments([_landed()], {}, {}, set())
    assert pending == []


def test_a_client_document_is_filed_for_each_owner_in_the_tenant() -> None:
    homes = {
        "111": [ClientHome(tenant_id=7, contact_id=3, bucket="t-a")],
        "222": [ClientHome(tenant_id=7, contact_id=4, bucket="t-a")],
    }
    document = _landed(party_ids=("111", "222"))
    pending = assignments([document], homes, {}, set())
    assert {(item.tenant_id, item.contact_id) for item in pending} == {(7, 3), (7, 4)}
    assert len({item.storage_key for item in pending}) == 1


def test_a_firm_pack_goes_to_every_tenant_of_the_firm_with_no_contact() -> None:
    document = _landed(document_id="firm", firm_level=True, party_ids=(), feed="firm-generic")
    homes = {"3106": [FirmHome(tenant_id=7, bucket="t-a"), FirmHome(tenant_id=8, bucket="t-b")]}
    pending = assignments([document], {}, homes, set())
    assert [(item.tenant_id, item.contact_id) for item in pending] == [(7, None), (8, None)]


def test_an_already_filed_copy_is_not_repeated() -> None:
    homes = {"111": [ClientHome(tenant_id=7, contact_id=3, bucket="t-a")]}
    pending = assignments([_landed()], homes, {}, {("a", 7, 3)})
    assert pending == []
