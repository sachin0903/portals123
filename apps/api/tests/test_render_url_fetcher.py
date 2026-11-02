"""What a document render may fetch (``app.render.renderers``): inline data and public http(s),
never a local file or an internal address.

The check itself is plain Python. The fetcher and the render need WeasyPrint's native stack
(Pango/Cairo/GLib) and skip where it cannot load — a Windows dev box, a runner without it.
"""

from urllib.request import Request

import pytest

from app.render import renderers

# A 1×1 PNG.
PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

needs_weasyprint = pytest.mark.skipif(
    not renderers.weasyprint_available()[0], reason="WeasyPrint's native stack is not here"
)


@pytest.mark.parametrize("url", [PNG_DATA_URL, "https://93.184.216.34/logo.png"])
def test_inline_data_and_a_public_address_may_be_fetched(url: str) -> None:
    renderers._check_document_resource(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://93.184.216.34/x",
        "http://127.0.0.1/x.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/x.png",
        "http://0.0.0.0/x.png",
    ],
)
def test_a_local_file_or_an_internal_address_is_refused(url: str) -> None:
    with pytest.raises(ValueError):
        renderers._check_document_resource(url)


@needs_weasyprint
def test_the_fetcher_hands_back_inline_data() -> None:
    response = renderers._safe_url_fetcher().fetch(PNG_DATA_URL)
    assert response.read().startswith(b"\x89PNG")


@needs_weasyprint
def test_the_fetcher_refuses_an_internal_address() -> None:
    with pytest.raises(ValueError):
        renderers._safe_url_fetcher().fetch("http://169.254.169.254/latest/meta-data/")


class _FetcherBase:
    """The part of WeasyPrint 70's ``URLFetcher`` the guard has to respect: a redirect arrives
    through ``open(Request)``, which parks the request for ``fetch`` to open."""

    def __init__(self, **kwargs: object) -> None:
        del kwargs
        self._request: Request | None = None
        self.opened: list[str] = []

    def fetch(self, url: str, headers: object = None) -> str:
        del headers
        http_request = self._request or Request(url)  # noqa: S310 — nothing is opened
        self._request = None
        self.opened.append(http_request.full_url)
        return http_request.full_url

    def open(self, url: Request) -> str:
        self._request = url
        return self.fetch(url.full_url, url.headers)


def test_a_refused_redirect_hop_is_not_opened_for_the_next_resource() -> None:
    """The hole: a refused hop stayed parked on the fetcher, and the next resource — whose own
    URL passed — was opened with the refused request. The same fetcher serves a whole render."""
    fetcher = renderers._safe_fetcher_class(_FetcherBase)()
    with pytest.raises(ValueError):
        fetcher.open(Request("http://169.254.169.254/latest/meta-data/"))
    assert fetcher._request is None
    assert fetcher.fetch(PNG_DATA_URL) == PNG_DATA_URL
    assert fetcher.opened == [PNG_DATA_URL]


@needs_weasyprint
def test_the_real_fetcher_forgets_a_refused_redirect_hop() -> None:
    fetcher = renderers._safe_url_fetcher()
    with pytest.raises(ValueError):
        fetcher.open(Request("http://127.0.0.1/x.png"))
    assert fetcher._request is None
    assert fetcher.fetch(PNG_DATA_URL).read().startswith(b"\x89PNG")


@needs_weasyprint
def test_a_refused_resource_is_left_out_and_the_document_still_renders() -> None:
    """The bug: on WeasyPrint 70 the fetcher was a plain function, and the first resource it
    refused crashed the whole render instead of leaving that one resource out."""
    html = (
        f'<p>Statement</p><img src="{PNG_DATA_URL}">'
        '<img src="http://127.0.0.1/x.png"><img src="file:///etc/passwd">'
    )
    pdf = renderers.WeasyPrintRenderer()._render_sync(html, "")
    assert pdf.startswith(b"%PDF")
