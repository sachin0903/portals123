"""The worker's synchronous preview-render service (``app.render_service``) and the API-side
``render_pdf`` delegation (``app.render.renderers``).

The heavy native render is mocked — these cover the wiring that makes a preview go through the SAME
engine as generation: the endpoint + its content-hash cache, and the API's delegate-or-local choice.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import render_service
from app.config import settings
from app.render import renderers


class _FakeRenderer:
    """Records each render so a test can assert the cache avoided a second one."""

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def to_pdf(
        self,
        html: str,
        css: str = "",
        branding_css: str = "",
        page_size: dict[str, str] | None = None,
        overlay_css: str = "",
    ) -> tuple[bytes, str]:
        self.calls.append(html)
        return b"%PDF-fake " + html.encode()[:16], "sha"


def test_render_endpoint_returns_pdf_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(render_service, "get_renderer", lambda name: _FakeRenderer(calls))
    render_service._cache.clear()
    client = TestClient(render_service.app)
    body = {"html": "<div class='page'><h1>Hi</h1></div>", "renderer": "weasy"}

    r1 = client.post("/internal/render", json=body)
    assert r1.status_code == 200
    assert r1.headers["content-type"] == "application/pdf"
    assert r1.content.startswith(b"%PDF-fake")

    # Identical body → cache hit → the renderer is NOT invoked a second time.
    r2 = client.post("/internal/render", json=body)
    assert r2.status_code == 200
    assert r2.content == r1.content
    assert len(calls) == 1

    # A different body misses the cache and renders again.
    client.post("/internal/render", json={"html": "<div class='page'><h1>Other</h1></div>"})
    assert len(calls) == 2


def test_render_health() -> None:
    assert TestClient(render_service.app).get("/internal/health").json() == {"ok": True}


@pytest.mark.asyncio
async def test_render_pdf_renders_locally_when_no_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """No RENDER_SERVICE_URL → the API renders in-process (the deploy-with-WeasyPrint path)."""
    calls: list[str] = []
    monkeypatch.setattr(settings, "render_service_url", "")
    monkeypatch.setattr(renderers, "get_renderer", lambda name: _FakeRenderer(calls))
    pdf, _sha = await renderers.render_pdf("<h1>local</h1>", renderer="weasy")
    assert pdf.startswith(b"%PDF-fake")
    assert calls == ["<h1>local</h1>"]


@pytest.mark.asyncio
async def test_render_pdf_delegates_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """RENDER_SERVICE_URL set → the API POSTs the HTML to the worker and returns its PDF bytes."""
    monkeypatch.setattr(settings, "render_service_url", "http://render:8001")
    seen: dict[str, object] = {}

    class _Resp:
        content = b"%PDF-delegated"

        def raise_for_status(self) -> None:
            pass

    class _Client:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def post(self, url: str, json: dict[str, object] | None = None) -> _Resp:
            seen["url"] = url
            seen["json"] = json
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    pdf, _sha = await renderers.render_pdf("<h1>x</h1>", css="p{}", renderer="weasy")
    assert pdf == b"%PDF-delegated"
    assert seen["url"] == "http://render:8001/internal/render"
    assert seen["json"] == {  # the exact payload the worker endpoint expects
        "html": "<h1>x</h1>",
        "css": "p{}",
        "branding_css": "",
        "page_size": None,
        "renderer": "weasy",
        "overlay_css": "",
    }
