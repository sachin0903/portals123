"""Chat-attachment storage: magic-byte sniffing (extended to Office/text formats) and the
tenant-bucket key layout. Pure logic — no live S3 (put/get against a tenant's own bucket are
exercised against the RustFS container via `pnpm storage:up`, not in unit tests, mirroring
test_storage_documents.py)."""

import io
import zipfile

import pytest

from app import storage

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj"


def _zip_with(*names: str) -> bytes:
    """A real, parseable zip containing empty entries at `names` — `_zip_document_kind` opens it
    for real, so a fixture has to be an actual zip, not just a magic-byte prefix."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        for name in names:
            zf.writestr(name, "")
    return buf.getvalue()


DOCX_ZIP = _zip_with("word/document.xml")
XLSX_ZIP = _zip_with("xl/workbook.xml")
PPTX_ZIP = _zip_with("ppt/presentation.xml")
UNRECOGNISED_ZIP = _zip_with("stuff/whatever.txt")


def test_sniff_recognises_pdf() -> None:
    assert storage.sniff_attachment_type(PDF) == "application/pdf"


def test_sniff_disambiguates_ooxml_by_internal_contents() -> None:
    """docx/xlsx/pptx share one outer zip signature — only the internal parts tell them apart."""
    assert storage.sniff_attachment_type(DOCX_ZIP) == storage.DOCX
    assert storage.sniff_attachment_type(XLSX_ZIP) == storage.XLSX
    assert storage.sniff_attachment_type(PPTX_ZIP) == storage.PPTX


def test_sniff_rejects_a_zip_that_matches_no_office_format() -> None:
    with pytest.raises(ValueError):
        storage.sniff_attachment_type(UNRECOGNISED_ZIP)
    with pytest.raises(ValueError):
        storage.sniff_attachment_type(b"PK\x03\x04not even a real zip")


def test_sniff_recognises_legacy_office_by_ole2_stream_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy .doc/.xls/.ppt share one OLE2 compound-file signature; disambiguated by the one
    top-level stream each format mandates (MS-DOC/-XLS/-PPT). Constructing a byte-perfect OLE2
    file is out of scope for a unit test, so `olefile` (already a real dependency) is mocked at
    its own boundary — this pins the stream-name -> content-type mapping, not olefile's parser."""
    import olefile

    ole2_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 8

    class _FakeOle:
        def __init__(self, streams: list[str]) -> None:
            self._streams = streams

        def listdir(self) -> list[list[str]]:
            return [[s] for s in self._streams]

        def close(self) -> None:
            pass

        def __enter__(self) -> "_FakeOle":
            return self

        def __exit__(self, *exc: object) -> None:
            self.close()

    def _ole_for(streams: list[str]) -> None:
        monkeypatch.setattr(olefile, "isOleFile", lambda data: True)
        monkeypatch.setattr(olefile, "OleFileIO", lambda data: _FakeOle(streams))

    _ole_for(["WordDocument"])
    assert storage.sniff_attachment_type(ole2_bytes) == storage.DOC

    _ole_for(["Workbook"])
    assert storage.sniff_attachment_type(ole2_bytes) == storage.XLS

    _ole_for(["PowerPoint Document"])
    assert storage.sniff_attachment_type(ole2_bytes) == storage.PPT

    _ole_for(["SomeOtherStream"])
    with pytest.raises(ValueError):
        storage.sniff_attachment_type(ole2_bytes)


def test_sniff_accepts_clean_text_by_elimination() -> None:
    assert storage.sniff_attachment_type(b"hello,world\n1,2,3\n") == storage.TXT


def test_sniff_rejects_binary_garbage_renamed_as_text() -> None:
    with pytest.raises(ValueError):
        storage.sniff_attachment_type(b"\x00\x01\x02binary\xff\xfe")
    with pytest.raises(ValueError):
        storage.sniff_attachment_type(b"\xff\xfe\x00\x01not-utf8")


def test_validate_attachment_upload_returns_type_and_ext() -> None:
    assert storage.validate_attachment_upload(PDF) == ("application/pdf", "pdf")
    assert storage.validate_attachment_upload(DOCX_ZIP) == (storage.DOCX, "docx")
    assert storage.validate_attachment_upload(b"plain text") == (storage.TXT, "txt")


def test_validate_attachment_upload_rejects_empty_and_oversize() -> None:
    with pytest.raises(ValueError):
        storage.validate_attachment_upload(b"")
    with pytest.raises(ValueError):
        storage.validate_attachment_upload(b"%PDF-" + b"x" * storage.MAX_DOCUMENT_BYTES)


def test_attachment_key_layout_is_private_not_under_files_tenant() -> None:
    key = storage.attachment_key(contact_uid="cli_abc123", attachment_uid="att_xyz789", ext="pdf")
    assert key == "files/cli_abc123/messages/att_xyz789.pdf"
    assert not key.startswith("files/tenant/")


def test_two_attachments_on_one_message_get_different_keys() -> None:
    """Keyed by the ATTACHMENT, not the message. A message may carry several files (0123), and a
    message-keyed path made the second silently overwrite the first in the object store — the row
    would still list both, and one of them would download the other's bytes."""
    first = storage.attachment_key(contact_uid="cli_abc123", attachment_uid="att_one", ext="pdf")
    second = storage.attachment_key(contact_uid="cli_abc123", attachment_uid="att_two", ext="pdf")
    assert first != second
