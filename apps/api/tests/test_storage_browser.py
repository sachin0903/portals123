"""Unit tests for the admin object browser (app.storage.list_objects). No real S3 — boto3 is
mocked. Pins the datalake exclusion (docs/storage.md: `lake/` must never be reachable), the
folder/object parsing, the folder-placeholder drop, and continuation-token passthrough."""

from datetime import UTC, datetime

import pytest

from app import storage


class _FakeClient:
    def __init__(self, resp: dict, capture: dict) -> None:
        self._resp = resp
        self._capture = capture

    def list_objects_v2(self, **kwargs: object) -> dict:
        self._capture.update(kwargs)
        return self._resp


_TS = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.mark.asyncio
async def test_list_objects_parses_and_excludes_datalake(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = {
        "CommonPrefixes": [
            {"Prefix": "documents/"},
            {"Prefix": "uploads/"},
            {"Prefix": "lake/"},  # datalake — must never be surfaced
            {"Prefix": "archive/"},  # archives — hidden
        ],
        "Contents": [
            {"Key": "root.txt", "Size": 12, "LastModified": _TS},
            {"Key": "lake/secret.parquet", "Size": 99, "LastModified": _TS},  # hidden
        ],
        "NextContinuationToken": "TOK",
    }
    capture: dict = {}
    monkeypatch.setattr(storage, "_client", lambda: _FakeClient(resp, capture))
    monkeypatch.setattr(storage, "documents_configured", lambda: True)

    page = await storage.list_objects("bkt", "", token=None, limit=50)

    assert page.folders == ["documents/", "uploads/"]  # lake/ + archive/ excluded
    assert [o.key for o in page.objects] == ["root.txt"]  # lake/secret.parquet excluded
    assert page.objects[0].size == 12
    assert page.objects[0].last_modified.startswith("2026-01-02")
    assert page.next_token == "TOK"  # noqa: S105
    # Bucket/delimiter/limit passed through; no ContinuationToken when token is None.
    assert capture["Bucket"] == "bkt" and capture["Delimiter"] == "/" and capture["MaxKeys"] == 50
    assert "ContinuationToken" not in capture


@pytest.mark.asyncio
async def test_list_objects_drops_placeholder_and_passes_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = {
        "CommonPrefixes": [],
        "Contents": [
            {"Key": "documents/", "Size": 0, "LastModified": _TS},  # placeholder == prefix
            {"Key": "documents/a.pdf", "Size": 5, "LastModified": _TS},
        ],
    }
    capture: dict = {}
    monkeypatch.setattr(storage, "_client", lambda: _FakeClient(resp, capture))
    monkeypatch.setattr(storage, "documents_configured", lambda: True)

    page = await storage.list_objects("bkt", "documents/", token="PREV", limit=10)  # noqa: S106

    assert [o.key for o in page.objects] == ["documents/a.pdf"]  # prefix-equal placeholder dropped
    assert page.next_token is None
    assert capture["ContinuationToken"] == "PREV" and capture["Prefix"] == "documents/"


@pytest.mark.asyncio
async def test_list_objects_missing_bucket_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bucket missing from the store surfaces as BucketMissing so a caller can fall back."""
    from botocore.exceptions import ClientError

    class _MissingBucketClient:
        def list_objects_v2(self, **kwargs: object) -> dict:
            raise ClientError(
                {"Error": {"Code": "NoSuchBucket", "Message": "gone"}}, "ListObjectsV2"
            )

    monkeypatch.setattr(storage, "_client", lambda: _MissingBucketClient())
    monkeypatch.setattr(storage, "documents_configured", lambda: True)

    with pytest.raises(storage.BucketMissing):
        await storage.list_objects("missing-bkt", "")


@pytest.mark.asyncio
async def test_list_objects_other_client_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only NoSuchBucket is swallowed — a real fault (e.g. AccessDenied) must still surface."""
    from botocore.exceptions import ClientError

    class _DeniedClient:
        def list_objects_v2(self, **kwargs: object) -> dict:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "ListObjectsV2")

    monkeypatch.setattr(storage, "_client", lambda: _DeniedClient())
    monkeypatch.setattr(storage, "documents_configured", lambda: True)

    with pytest.raises(ClientError):
        await storage.list_objects("bkt", "")


@pytest.mark.asyncio
async def test_browser_falls_back_to_shared_when_tenant_bucket_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admin browser lists the shared documents bucket (where objects live) when a tenant's
    registry bucket was never minted, instead of 500ing (the deployment regression)."""
    from app.auth import Principal
    from app.routers import admin

    async def _row(_session: object, _tid: int) -> dict:
        return {"status": "active", "bucket": "t-missing"}

    async def _list(
        bucket: str,
        prefix: str = "",
        *,
        token: object = None,
        limit: int = 100,
        delimiter: str = "/",
    ) -> storage.ObjectPage:
        if bucket == "t-missing":
            raise storage.BucketMissing(bucket)
        return storage.ObjectPage(objects=[], folders=[], next_token=None)

    monkeypatch.setattr(admin.tenant_storage_mod, "get_storage_row", _row)
    monkeypatch.setattr(storage, "list_objects", _list)
    monkeypatch.setattr(storage, "documents_bucket", lambda: "portals-documents")

    principal = Principal(
        user_id="op", organisation_id="org", role="operations", permissions=["book:read"]
    )
    out = await admin.list_tenant_storage_objects(7, session=object(), _p=principal)  # type: ignore[arg-type]

    assert out.bucket == "portals-documents"
    assert out.per_tenant is False
    assert out.objects == []


def test_is_hidden_covers_datalake_and_archive() -> None:
    assert storage.is_hidden("lake/x")
    assert storage.is_hidden("datalake/y")
    assert storage.is_hidden("archive/z")
    assert not storage.is_hidden("documents/a.pdf")
    assert not storage.is_hidden("uploads/t/doc/original.pdf")


@pytest.mark.asyncio
async def test_list_objects_requires_configured_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage, "documents_configured", lambda: False)
    with pytest.raises(storage.StorageUnavailable):
        await storage.list_objects("bkt", "")


def test_ext_of() -> None:
    assert storage.ext_of("a/b/Report.PDF") == "pdf"  # lowercased
    assert storage.ext_of("uploads/statement.2026.xlsx") == "xlsx"  # last dot wins
    assert storage.ext_of("documents/noext") == ""  # no extension
    assert storage.ext_of("documents/.hidden") == ""  # leading dot only → no extension
    assert storage.ext_of("documents/") == ""  # folder placeholder


_TS2 = datetime(2026, 3, 1, tzinfo=UTC)  # later than _TS — must win as last_modified


@pytest.mark.asyncio
async def test_prefix_stats_aggregates_recursively(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rolls size/count/latest-modified/types up over every object under the prefix, skipping the
    folder placeholder and hidden (datalake/archive) keys."""
    resp = {
        "Contents": [
            {"Key": "documents/a.pdf", "Size": 10, "LastModified": _TS},
            {"Key": "documents/sub/b.XLSX", "Size": 20, "LastModified": _TS2},  # latest; ext xlsx
            {"Key": "documents/", "Size": 0, "LastModified": _TS},  # placeholder — skipped
            {"Key": "documents/note.pdf", "Size": 5, "LastModified": _TS},
            {"Key": "lake/leak.parquet", "Size": 99, "LastModified": _TS2},  # hidden — skipped
        ],
    }
    monkeypatch.setattr(storage, "_client", lambda: _FakeClient(resp, {}))
    monkeypatch.setattr(storage, "documents_configured", lambda: True)

    st = await storage.prefix_stats("bkt", "documents/")

    assert st.size == 35  # 10 + 20 + 5 (placeholder + hidden excluded)
    assert st.count == 3
    assert st.last_modified is not None and st.last_modified.startswith("2026-03-01")
    assert st.exts == ("pdf", "xlsx")  # sorted, distinct
    assert st.truncated is False


@pytest.mark.asyncio
async def test_prefix_stats_truncates_at_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A folder larger than the cap stops early and flags `truncated` (totals are a lower bound)."""
    resp = {
        "Contents": [
            {"Key": f"documents/f{i}.pdf", "Size": 1, "LastModified": _TS} for i in range(5)
        ],
    }
    monkeypatch.setattr(storage, "_client", lambda: _FakeClient(resp, {}))
    monkeypatch.setattr(storage, "documents_configured", lambda: True)

    st = await storage.prefix_stats("bkt", "documents/", cap=3)

    assert st.count == 3 and st.size == 3  # stopped at the cap
    assert st.truncated is True
