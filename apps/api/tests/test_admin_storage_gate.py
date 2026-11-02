"""The admin storage-object browser's permission gate (permission audit M3/G6).

`GET /admin/tenants/{tenant_uid}/storage/objects` and `…/storage/object-url` surface (and presign) a
firm's CLIENT files — book data, not tenant configuration — so they gate on `book:read`
(operations-only), not `tenants:read`. Tested over real HTTP so the `Depends(_book_read)`
wiring itself is what's exercised: calling the coroutines directly would bypass the gate, which
is exactly how the original hole slipped through review.

No database: `admin_session` is dependency-overridden (its coarse operations gate has its own
suite) and the storage layer is monkeypatched, so this file pins the granular permission matrix
and nothing else.
"""

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import storage
from app.addressing import admin_tenant_id
from app.auth import Principal, get_current_principal
from app.db import admin_session
from app.main import app
from app.routers import admin as admin_router


@pytest_asyncio.fixture
async def http() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def acting(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Installs a principal + a stub admin session; storage reads are stubbed to empty."""

    async def _no_session() -> AsyncIterator[object]:
        yield object()  # never touched on these paths (labels resolve nothing for empty pages)

    app.dependency_overrides[admin_session] = _no_session
    # The routes are `/admin/tenants/{tenant_uid}/…` now, and the uid is turned into the serial by
    # a dependency that reads the database. These cases are about the PERMISSION gate, not the
    # lookup, so the resolve is stubbed rather than seeded — the stub session above answers nothing.
    app.dependency_overrides[admin_tenant_id] = lambda: 7

    async def _no_row(_session: object, _tid: int) -> None:
        return None  # no per-tenant bucket → the shared documents bucket

    async def _empty_list(
        bucket: str,
        prefix: str = "",
        *,
        token: object = None,
        limit: int = 100,
        delimiter: str = "/",
    ) -> storage.ObjectPage:
        return storage.ObjectPage(objects=[], folders=[], next_token=None)

    async def _signed(
        key: str, *, filename: str, disposition: str, bucket: str | None = None
    ) -> str:
        return "https://signed.example/obj"

    monkeypatch.setattr(admin_router.tenant_storage_mod, "get_storage_row", _no_row)
    monkeypatch.setattr(storage, "documents_bucket", lambda: "portals-documents")
    monkeypatch.setattr(storage, "list_objects", _empty_list)
    monkeypatch.setattr(storage, "presigned_get_document", _signed)

    def act(perms: list[str]) -> None:
        def override() -> Principal:
            return Principal(user_id="user_ops", role="operations", permissions=list(perms))

        app.dependency_overrides[get_current_principal] = override

    yield act
    app.dependency_overrides.pop(get_current_principal, None)
    app.dependency_overrides.pop(admin_session, None)
    app.dependency_overrides.pop(admin_tenant_id, None)


@pytest.mark.asyncio
async def test_object_browser_refuses_tenants_read_alone(http: AsyncClient, acting) -> None:  # type: ignore[no-untyped-def]
    """`tenants:read` means "may administer firms"; it must NOT open a firm's client files."""
    acting(["tenants:read"])
    r = await http.get("/admin/tenants/ten_seven/storage/objects")
    assert r.status_code == 403
    assert "book:read" in r.json()["detail"]

    r = await http.get(
        "/admin/tenants/ten_seven/storage/object-url", params={"key": "documents/a.pdf"}
    )
    assert r.status_code == 403
    assert "book:read" in r.json()["detail"]


@pytest.mark.asyncio
async def test_object_browser_opens_for_book_read(http: AsyncClient, acting) -> None:  # type: ignore[no-untyped-def]
    acting(["book:read"])
    r = await http.get("/admin/tenants/ten_seven/storage/objects")
    assert r.status_code == 200
    assert r.json()["bucket"] == "portals-documents"

    r = await http.get(
        "/admin/tenants/ten_seven/storage/object-url", params={"key": "documents/a.pdf"}
    )
    assert r.status_code == 200
    assert r.json()["url"] == "https://signed.example/obj"
