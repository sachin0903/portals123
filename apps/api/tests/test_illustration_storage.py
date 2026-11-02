"""Filing the PDF: what happens when object storage will not take it.

The endpoint needs Postgres for the rest, but this one branch is worth pinning on its own. An
illustration that was produced and could not be filed must come back as a stated outcome the UI can
explain, not as a 500 — and only the UPLOAD is allowed to degrade that way. A failed INSERT is a
real error and must still surface as one.
"""

from typing import Any

import pytest
from fastapi import HTTPException, status

from app import storage
from app.routers import illustrations


class _Session:
    """Just enough session for `_store_pdf` to reach the upload: it asks for auth.tenant_id()."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append(str(statement))

        class _Result:
            def scalar_one_or_none(self) -> int:
                return 1

            def scalar_one(self) -> int:  # pragma: no cover - not reached in these tests
                return 1

        return _Result()


@pytest.mark.asyncio
async def test_unconfigured_storage_names_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        raise storage.StorageUnavailable(storage.SHOWN_WHEN_UNCONFIGURED)

    monkeypatch.setattr(storage, "put_document", refuse)

    with pytest.raises(HTTPException) as caught:
        await illustrations._store_pdf(
            _Session(),  # type: ignore[arg-type]
            contact_id=1,
            pdf=b"%PDF-1.4",
            name="SIPP illustration",
            inputs={},
        )
    assert caught.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert str(caught.value.detail) == "Document storage is unavailable. Contact support."


@pytest.mark.asyncio
async def test_an_unreachable_store_does_not_claim_to_be_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler passes the exception's own message through rather than asserting a cause.

    It used to answer every `StorageUnavailable` with "not configured (S3_DOCUMENTS_BUCKET)",
    which is a diagnosis, and it was the wrong one for a store that is configured and not
    answering — the state dev sat in for two days while every upload failed."""

    async def refuse(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        raise storage.StorageUnreachable("Document storage is temporarily unavailable.")

    monkeypatch.setattr(storage, "put_document", refuse)

    with pytest.raises(HTTPException) as caught:
        await illustrations._store_pdf(
            _Session(),  # type: ignore[arg-type]
            contact_id=1,
            pdf=b"%PDF-1.4",
            name="SIPP illustration",
            inputs={},
        )
    assert caught.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "S3_DOCUMENTS_BUCKET" not in str(caught.value.detail)


@pytest.mark.asyncio
async def test_a_configured_store_that_refuses_the_write_is_also_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing bucket or bad credentials raises a boto error, which used to become a 500."""

    async def refuse(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        raise RuntimeError("NoSuchBucket: the specified bucket does not exist")

    monkeypatch.setattr(storage, "put_document", refuse)

    with pytest.raises(HTTPException) as caught:
        await illustrations._store_pdf(
            _Session(),  # type: ignore[arg-type]
            contact_id=1,
            pdf=b"%PDF-1.4",
            name="SIPP illustration",
            inputs={},
        )
    assert caught.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "NoSuchBucket" in str(caught.value.detail)


@pytest.mark.asyncio
async def test_no_tenant_context_is_a_403_not_a_missing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NoTenant(_Session):
        async def execute(self, statement: Any, params: Any = None) -> Any:
            class _Result:
                def scalar_one_or_none(self) -> None:
                    return None

            return _Result()

    uploaded: list[str] = []

    async def record(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        uploaded.append(key)

    monkeypatch.setattr(storage, "put_document", record)

    with pytest.raises(HTTPException) as caught:
        await illustrations._store_pdf(
            _NoTenant(),  # type: ignore[arg-type]
            contact_id=1,
            pdf=b"%PDF-1.4",
            name="SIPP illustration",
            inputs={},
        )
    assert caught.value.status_code == status.HTTP_403_FORBIDDEN
    assert uploaded == []  # nothing is written outside a tenant


class _Filing(_Session):
    """A session that answers the firm's `illustrations_auto_share` flag and records every write,
    so the test can say whether the client was given the document."""

    def __init__(self, *, auto_share: bool) -> None:
        super().__init__()
        self.auto_share = auto_share

    async def execute(self, statement: Any, params: Any = None) -> Any:
        sql = str(statement)
        self.statements.append(sql)
        answer: Any = self.auto_share if "illustrations_auto_share" in sql else 1

        class _Result:
            def scalar_one_or_none(self) -> Any:
                return answer

            def scalar_one(self) -> Any:
                return answer

        return _Result()


@pytest.mark.parametrize("auto_share", [False, True])
@pytest.mark.asyncio
async def test_the_client_sees_a_sipp_illustration_only_if_the_firm_shares_on_render(
    monkeypatch: pytest.MonkeyPatch, auto_share: bool
) -> None:
    """FB-20261006-04. `_store_pdf` bound the 'subject' link — the client's read permission — on
    every render. Now the document is filed FOR the client (`intended_contact_id`) and the link is
    bound only for a firm that has opted in; otherwise staff share it from the record."""

    async def accept(key: str, data: bytes, content_type: str, **_kw: object) -> None:
        return None

    monkeypatch.setattr(storage, "put_document", accept)
    session = _Filing(auto_share=auto_share)

    await illustrations._store_pdf(
        session,  # type: ignore[arg-type]
        contact_id=7,
        pdf=b"%PDF-1.4",
        name="SIPP illustration",
        inputs={},
    )

    inserted = [s for s in session.statements if "INSERT INTO documents.documents" in s]
    assert len(inserted) == 1 and "intended_contact_id" in inserted[0]
    linked = [s for s in session.statements if "INSERT INTO documents.document_contacts" in s]
    assert bool(linked) is auto_share
