"""Private-document storage: magic-byte sniffing, upload validation, key layout, and the
unconfigured guard. Pure logic — no live S3 (put/get are exercised against the RustFS container
via `pnpm storage:up`, not in unit tests)."""

import io
import zipfile
from pathlib import Path

import pytest

from app import storage
from app.storage import DOCX, StorageUnavailable

# Minimal valid bytes for each non-container type.
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
GIF = b"GIF89a\x01\x00\x01\x00"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 "
TIFF = b"II*\x00\x08\x00\x00\x00"
BMP = b"BM\x1a\x00\x00\x00"
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00"
AVIF = b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00"


def _zip_with(*names: str, mimetype: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        if mimetype:
            zf.writestr("mimetype", mimetype)
        for name in names:
            zf.writestr(name, "")
    return buf.getvalue()


DOCX_ZIP = _zip_with("word/document.xml")
XLSX_ZIP = _zip_with("xl/workbook.xml")
PPTX_ZIP = _zip_with("ppt/presentation.xml")
ODT_ZIP = _zip_with(mimetype=storage.ODT)
UNKNOWN_ZIP = _zip_with("files/data.bin")


def test_sniff_recognises_allowed_types() -> None:
    assert storage.sniff_document_type(PDF) == "application/pdf"
    assert storage.sniff_document_type(PNG) == "image/png"
    assert storage.sniff_document_type(JPEG) == "image/jpeg"
    assert storage.sniff_document_type(GIF) == "image/gif"
    assert storage.sniff_document_type(WEBP) == "image/webp"
    assert storage.sniff_document_type(TIFF) == "image/tiff"
    assert storage.sniff_document_type(BMP) == "image/bmp"
    assert storage.sniff_document_type(HEIC) == storage.HEIC
    assert storage.sniff_document_type(AVIF) == storage.AVIF
    assert storage.sniff_document_type(DOCX_ZIP) == DOCX
    assert storage.sniff_document_type(XLSX_ZIP) == storage.XLSX
    assert storage.sniff_document_type(PPTX_ZIP) == storage.PPTX
    assert storage.sniff_document_type(ODT_ZIP) == storage.ODT
    assert storage.sniff_document_type(b"{\\rtf1 common rich text}") == storage.RTF
    assert storage.sniff_document_type(b"plain notes") == storage.TXT
    assert (
        storage.sniff_document_type(b"name,amount\nISA,100\n", declared="text/csv") == storage.CSV
    )


def test_sniff_zip_is_classified_by_contents_not_the_declared_type() -> None:
    assert storage.sniff_document_type(DOCX_ZIP, declared="application/zip") == DOCX
    with pytest.raises(ValueError):
        storage.sniff_document_type(UNKNOWN_ZIP, declared=DOCX)


def test_sniff_recognises_legacy_word_excel_and_powerpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import olefile

    ole2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 8

    class FakeOle:
        def __init__(self, stream: str) -> None:
            self.stream = stream

        def listdir(self) -> list[list[str]]:
            return [[self.stream]]

        def __enter__(self) -> "FakeOle":
            return self

        def __exit__(self, *exc: object) -> None:
            pass

    monkeypatch.setattr(olefile, "isOleFile", lambda *, data: True)
    for stream, expected in (
        ("WordDocument", storage.DOC),
        ("Workbook", storage.XLS),
        ("PowerPoint Document", storage.PPT),
    ):
        monkeypatch.setattr(olefile, "OleFileIO", lambda _data, stream=stream: FakeOle(stream))
        assert storage.sniff_document_type(ole2) == expected


def test_sniff_rejects_unknown_and_spoofed_content() -> None:
    with pytest.raises(ValueError):
        storage.sniff_document_type(b"<html>not a pdf</html>")
    # A client claiming PDF but sending an executable is caught by bytes, not the declaration.
    with pytest.raises(ValueError):
        storage.sniff_document_type(b"MZ\x90\x00executable", declared="application/pdf")


def test_validate_upload_returns_type_and_ext() -> None:
    assert storage.validate_upload(PDF) == ("application/pdf", "pdf")
    assert storage.validate_upload(PNG) == ("image/png", "png")
    assert storage.validate_upload(JPEG) == ("image/jpeg", "jpg")
    assert storage.validate_upload(GIF) == ("image/gif", "gif")
    assert storage.validate_upload(DOCX_ZIP) == (DOCX, "docx")
    assert storage.validate_upload(XLSX_ZIP) == (storage.XLSX, "xlsx")
    assert storage.validate_upload(b"a,b\n1,2\n", declared="text/csv") == (storage.CSV, "csv")


def test_a_key_names_its_content_type() -> None:
    assert storage.content_type_of("uploads/abc/original.pdf") == "application/pdf"
    assert storage.content_type_of("uploads/abc/original.JPG") == "image/jpeg"
    assert storage.content_type_of("idv/front.jpeg") == "image/jpeg"
    assert storage.content_type_of("idv/front.png") == "image/png"
    assert storage.content_type_of("idv/front.exe") == "application/octet-stream"
    assert storage.content_type_of("idv/front") == "application/octet-stream"


def test_validate_upload_rejects_empty_and_oversize() -> None:
    with pytest.raises(ValueError):
        storage.validate_upload(b"")
    with pytest.raises(ValueError):
        storage.validate_upload(b"%PDF-" + b"x" * storage.MAX_DOCUMENT_BYTES)


def test_document_ext_mapping() -> None:
    assert storage.document_ext("application/pdf") == "pdf"
    assert storage.document_ext(DOCX) == "docx"
    assert storage.document_ext(storage.PPTX) == "pptx"
    assert storage.document_ext(storage.DOC) == "doc"
    assert storage.document_ext(storage.XLS) == "xls"
    assert storage.document_ext(storage.PPT) == "ppt"
    assert storage.document_ext(storage.ODT) == "odt"
    assert storage.document_ext("image/webp") == "webp"
    with pytest.raises(ValueError):
        storage.document_ext("application/x-msdownload")


def test_key_layout_is_path_addressed() -> None:
    """The LEGACY shape, which live rows still point at — it must not drift."""
    assert storage.upload_key("ten_a", "doc_1", "pdf") == "uploads/ten_a/doc_1/original.pdf"
    assert storage.rendered_key("ten_a", "doc_1", 2, "pdf") == "documents/ten_a/doc_1/v2.pdf"
    assert storage.rendered_key("ten_a", "doc_1", 1, "html") == "documents/ten_a/doc_1/v1.html"


def test_tenant_keys_carry_no_tenant_segment() -> None:
    """The bucket IS the tenant, so repeating it in the key would make every key longer than the
    thing it identifies — and everything sits under `files/`, which is the prefix 0061's
    `documents`/`readonly` service users are scoped to."""
    assert storage.tenant_upload_key("doc_1", "pdf") == "files/documents/doc_1/original.pdf"
    assert storage.tenant_rendered_key("doc_1", 2, "pdf") == "files/documents/doc_1/v2.pdf"
    assert storage.tenant_rendered_key("doc_1", 1, "html") == "files/documents/doc_1/v1.html"
    # A client's own upload files under them: subject-access and erasure both ask "what do you
    # hold about this person?", which is one prefix or a scan.
    assert (
        storage.tenant_upload_key("doc_1", "pdf", contact_uid="con_9")
        == "files/clients/con_9/doc_1/original.pdf"
    )


@pytest.mark.asyncio
async def test_reads_target_the_bucket_the_row_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tenant-bucket object is read from THAT bucket, and a legacy row from the shared one.

    This is the regression the whole writers-and-readers-together change exists to prevent: a
    tenant key carries no tenant segment, so a read pointed at the wrong bucket does not degrade —
    it 404s, and a 404 reads as "the document isn't there" rather than as the wrong bucket.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "s3_documents_bucket", "portals-documents")
    seen: list[tuple[str, str]] = []

    class _Client:
        def get_object(self, Bucket: str, Key: str) -> dict[str, object]:  # noqa: N803 — boto3 shape
            seen.append((Bucket, Key))
            return {"Body": io.BytesIO(PDF), "ContentType": "application/pdf"}

    monkeypatch.setattr(storage, "_client", lambda: _Client())

    await storage.get_document("files/documents/doc_1/original.pdf", bucket="tnt-abc123")
    await storage.get_document("uploads/ten_a/doc_1/original.pdf")  # legacy row: no bucket recorded

    assert seen == [
        ("tnt-abc123", "files/documents/doc_1/original.pdf"),
        ("portals-documents", "uploads/ten_a/doc_1/original.pdf"),
    ]


@pytest.mark.asyncio
async def test_an_explicit_bucket_needs_no_shared_bucket_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment that has moved wholly onto tenant buckets may leave S3_DOCUMENTS_BUCKET blank,
    so an explicit bucket must not trip the unconfigured guard."""
    from app.config import settings

    monkeypatch.setattr(settings, "s3_documents_bucket", "")
    put: list[str] = []

    class _Client:
        def put_object(self, **kwargs: object) -> None:
            put.append(str(kwargs["Bucket"]))

    monkeypatch.setattr(storage, "_client", lambda: _Client())
    await storage.put_document(
        "files/documents/d/original.pdf", PDF, "application/pdf", bucket="tnt-abc123"
    )
    assert put == ["tnt-abc123"]


def test_documents_unconfigured_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "s3_documents_bucket", "")
    assert storage.documents_configured() is False
    monkeypatch.setattr(settings, "s3_documents_bucket", "portals-documents")
    assert storage.documents_configured() is True


@pytest.mark.asyncio
async def test_put_document_raises_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """No data-URI fallback for documents: an unconfigured bucket is a hard StorageUnavailable."""
    from app.config import settings

    monkeypatch.setattr(settings, "s3_documents_bucket", "")
    with pytest.raises(StorageUnavailable):
        await storage.put_document("uploads/x/y/original.pdf", PDF, "application/pdf")
    with pytest.raises(StorageUnavailable):
        await storage.presigned_get_document("uploads/x/y/original.pdf")
    with pytest.raises(StorageUnavailable):
        await storage.get_document("uploads/x/y/original.pdf")


@pytest.mark.asyncio
async def test_an_unreachable_store_is_not_reported_as_an_unconfigured_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured store that does not answer must say so.

    The two failures are one outcome for the caller (503, no bytes stored) and opposite
    instructions for whoever fixes it. Dev spent two days with a perfectly-set
    `S3_DOCUMENTS_BUCKET` pointed at an object store that was never started, and every upload
    reported that the bucket was not configured — so the message sent an operator to check the one
    thing that was already right.
    """
    from botocore.exceptions import EndpointConnectionError

    from app.config import settings

    monkeypatch.setattr(settings, "s3_documents_bucket", "portals-documents")

    class _Unreachable:
        def put_object(self, **kwargs: object) -> None:
            raise EndpointConnectionError(endpoint_url="http://rustfs:9000")

    monkeypatch.setattr(storage, "_client", lambda: _Unreachable())

    with pytest.raises(storage.StorageUnreachable) as raised:
        await storage.put_document("files/documents/d/original.pdf", PDF, "application/pdf")

    # A subclass, so every handler's `except StorageUnavailable` keeps answering 503 unchanged.
    assert isinstance(raised.value, StorageUnavailable)
    assert "not configured" not in str(raised.value)
    # The endpoint is infrastructure detail and the message reaches a client portal verbatim.
    assert "rustfs" not in str(raised.value)


def test_the_unconfigured_message_lives_in_one_place() -> None:
    """Nine handlers across three routers answer 503 with `str(exc)`, so the wording is the
    exception's. Two spellings of it is how one of them keeps saying "not configured" for a store
    that is configured."""
    text = Path(storage.__file__).read_text()
    assert text.count('"Document storage is unavailable. Contact support."') == 1
    assert "StorageUnavailable(SHOWN_WHEN_UNCONFIGURED)" in text


@pytest.mark.asyncio
async def test_a_missing_object_is_object_missing_not_a_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row whose bytes the store no longer has used to let botocore's ``NoSuchKey`` propagate,
    which the routers answered as a 500 the portals dressed as "Document unavailable" — the same
    words as an outage. It is a typed miss now, and only the missing-object codes become one."""
    from botocore.exceptions import ClientError

    from app.config import settings

    monkeypatch.setattr(settings, "s3_documents_bucket", "portals-documents")

    def _raising(code: str):
        class _Client:
            def get_object(self, Bucket: str, Key: str) -> dict[str, object]:  # noqa: N803
                raise ClientError({"Error": {"Code": code, "Message": code}}, "GetObject")

        return lambda: _Client()

    monkeypatch.setattr(storage, "_client", _raising("NoSuchKey"))
    with pytest.raises(storage.ObjectMissing):
        await storage.get_document("uploads/x/y/original.pdf")

    monkeypatch.setattr(storage, "_client", _raising("AccessDenied"))
    with pytest.raises(ClientError):
        await storage.get_document("uploads/x/y/original.pdf")
