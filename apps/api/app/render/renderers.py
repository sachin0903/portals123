"""Document renderers — (html, css) → (pdf_bytes, sha256). Ported from platform-1.

Two engines behind one protocol: **WeasyPrint** (default) and **Playwright/Chromium** (opt-in).
The native deps (``weasyprint`` / ``playwright``) are imported lazily inside the render calls, so
importing this module never requires the heavy native stack (tests mock ``to_pdf``).
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit


@runtime_checkable
class DocumentRenderer(Protocol):
    """Convert (html, css) → (pdf_bytes, sha256_hex)."""

    async def to_pdf(
        self,
        html: str,
        css: str = "",
        branding_css: str = "",
        page_size: dict[str, str] | None = None,
        overlay_css: str = "",
    ) -> tuple[bytes, str]: ...


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_document_resource(url: str) -> None:
    """Refuse a document resource a render must not fetch, by raising ``ValueError``.

    A template's HTML is caller-supplied (the preview endpoint renders unsaved markup), so an
    ``<img src>`` / ``@import`` / ``background-image`` could otherwise make the worker fetch an
    internal URL (SSRF) or read a local file (LFI). This narrows what a render may fetch:

      * inline ``data:`` URIs pass straight through (the common case — logos, fonts);
      * ``http(s)`` is allowed ONLY to a public address, so a tenant's object-storage logo still
        loads, but loopback / link-local (incl. the ``169.254.169.254`` cloud-metadata endpoint) /
        multicast / reserved / unspecified targets are refused;
      * every other scheme (``file:``, ``ftp:``, …) is refused outright — closing local-file reads.

    Note: DNS is resolved here and again by the fetch itself, so this is not rebinding-proof — it
    blocks the obvious internal targets, not a resolver racing itself."""
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme == "data":
        return
    if scheme not in ("http", "https"):
        raise ValueError(f"blocked document resource scheme: {scheme or '(none)'!r}")
    host = parts.hostname or ""
    try:
        addrinfo = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"unresolvable document resource host: {host!r}") from exc
    for info in addrinfo:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_loopback
            or ip.is_link_local  # 169.254.0.0/16 — includes the cloud-metadata endpoint
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"blocked internal document resource address: {ip}")


def _safe_url_fetcher() -> Any:
    """A WeasyPrint URL fetcher a document render can't use to reach the host's own infra.

    WeasyPrint 70 takes a ``URLFetcher`` instance, not a function: it reads the fetcher's own
    settings when a fetch fails and asserts on the response type, so the plain function this was
    before crashed the render on the first resource it refused. A refusal (``ValueError``) is now a
    missing resource again — the page renders without it — which is what makes blocking fail safe.

    Every fetch is checked, a redirect's next hop included: the redirect handler re-enters
    ``fetch``. A fresh instance per render, because WeasyPrint keeps the request it is following on
    the fetcher, and renders run on worker threads."""
    from weasyprint.urls import URLFetcher  # type: ignore[import-untyped]  # lazy: native stack

    return _safe_fetcher_class(URLFetcher)(allowed_protocols=("data", "http", "https"))


def _safe_fetcher_class(base: type) -> type:
    """``base`` (WeasyPrint's ``URLFetcher``) with every URL checked before it is opened.

    A redirect reaches ``fetch`` through ``open(Request)``, which first parks that ``Request`` on
    ``self._request`` for ``fetch`` to pick up. Refusing the hop before ``super().fetch`` runs
    would leave it parked, and the NEXT resource on this fetcher — one whose own URL passed the
    check — would be opened with the refused request instead. So a refusal clears it first."""

    class SafeURLFetcher(base):  # type: ignore[misc]  # untyped base
        def fetch(self, url: str, headers: Any = None) -> Any:
            try:
                _check_document_resource(url)
            except BaseException:
                self._request = None
                raise
            return super().fetch(url, headers)

    return SafeURLFetcher


# ── WeasyPrint ────────────────────────────────────────────────────────────────
# A4 body padding that matches the editor canvas (72px); full-bleed blocks use margin:-72px.
_BODY_PADDING_PX = 72
# Pin to a metric-compatible Helvetica clone for stable re-renders (needs fonts-liberation).
_PINNED_FONT_FAMILY = "Liberation Sans, Arial, sans-serif"

# Static base CSS — no page-size values (those are injected per-render by _make_page_css). Copied
# verbatim from the prototype: it normalises the GrapesJS canvas output so editor and PDF match.
_BASE_CSS_STATIC = f"""
html {{ background: #fff; }}
body {{
    margin: 0; padding: 0; box-sizing: border-box;
    font-family: {_PINNED_FONT_FAMILY};
    font-size: 13px; color: #111827; line-height: 1.5; background: #fff;
}}
.page {{
    padding: {_BODY_PADDING_PX}px {_BODY_PADDING_PX}px 40px;
    box-sizing: border-box; position: relative; overflow: visible;
    page-break-after: always; break-after: page;
}}
tr {{ page-break-inside: avoid; break-inside: avoid; }}
h1 {{ font-size: 24px; font-weight: 700; line-height: 1.2; margin: 0 0 12px; }}
h2 {{ font-size: 18px; font-weight: 600; line-height: 1.3; margin: 0 0 8px; }}
h3 {{ font-size: 15px; font-weight: 600; line-height: 1.4; margin: 0 0 6px; }}
h4, h5, h6 {{ font-size: 13px; font-weight: 600; line-height: 1.5; margin: 0 0 4px; }}
header.tmpl-header, .tmpl-header {{
  position: absolute !important; top: 0; left: 0; right: 0;
  padding: 8px {_BODY_PADDING_PX}px; border-bottom: 1px solid rgba(0,0,0,0.12);
  display: flex; justify-content: space-between; align-items: center;
  font-size: 10px; background: transparent; box-sizing: border-box;
}}
header.tmpl-header > span, .tmpl-header > span {{ flex: 1; min-width: 0; }}
header.tmpl-header > span:first-child, .tmpl-header > span:first-child {{ text-align: left; }}
header.tmpl-header > span:nth-child(2), .tmpl-header > span:nth-child(2) {{ text-align: center; }}
header.tmpl-header > span:last-child, .tmpl-header > span:last-child {{ text-align: right; }}
footer.tmpl-footer, .tmpl-footer {{
  position: absolute !important; bottom: 0; left: 0; right: 0;
  padding: 8px {_BODY_PADDING_PX}px; border-top: 1px solid rgba(0,0,0,0.12);
  display: flex; justify-content: space-between; align-items: center;
  font-size: 10px; background: transparent; box-sizing: border-box;
}}
footer.tmpl-footer > span, .tmpl-footer > span {{ flex: 1; min-width: 0; }}
footer.tmpl-footer > span:first-child, .tmpl-footer > span:first-child {{ text-align: left; }}
footer.tmpl-footer > span:nth-child(2), .tmpl-footer > span:nth-child(2) {{ text-align: center; }}
footer.tmpl-footer > span:last-child, .tmpl-footer > span:last-child {{ text-align: right; }}
* {{ box-sizing: border-box; }}
.gjs-row {{ display: flex; align-items: stretch; flex-wrap: nowrap; padding: 0; }}
.gjs-cell {{ flex: 1; min-width: 0; overflow: hidden; }}
table {{ table-layout: fixed; max-width: 100%; width: 100%; word-break: break-word; }}
td, th {{ overflow: hidden; max-width: 0; }}
img {{ max-width: 100%; height: auto; display: block; }}
svg {{ max-width: 100%; }}
.doc-placeholder {{ display: none; }}
._wpc-page::before {{ content: counter(page); }}
._wpc-pages::before {{ content: counter(pages); }}
"""

_DOCUMENT_WRAPPER = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
{style_block}
</head>
<body>
{body}
</body>
</html>"""


def _make_page_css(width: str = "210mm", height: str = "297mm") -> str:
    return f"@page {{ size: {width} {height}; margin: 0; }}\n.page {{ min-height: {height}; }}\n"


# On-screen preview only (never the PDF): a soft drop shadow + a grey gap separates the stacked
# pages so each full page reads clearly in the iframe. Each page is clipped to its own rectangle
# (overflow:hidden) so the running header/footer rule — absolutely positioned to the page edges —
# breaks cleanly at the gap instead of bleeding across it. The scrollbars are hidden (but the doc
# still scrolls) so the vertical scrollbar can't steal width and push a page off horizontally; page
# width stays full-bleed so the preview still matches the PDF layout 1:1.
_PREVIEW_CHROME_CSS = """
html { scrollbar-width: none; background: #e2e8f0; }
html::-webkit-scrollbar { width: 0; height: 0; display: none; }
body { background: #e2e8f0; overflow-x: hidden; }
.page { background: #fff; box-shadow: 0 6px 18px rgba(0,0,0,0.10); margin: 0 auto 32px;
        overflow: hidden; }
.page:last-child { margin-bottom: 0; }
"""


def _wrap_html(fragment: str, css: str) -> str:
    """Return a complete HTML document from a template fragment + CSS (no double-wrap)."""
    if fragment.strip().lower().startswith("<!doctype") or "<html" in fragment[:200].lower():
        return fragment
    style_block = f"<style>{css}</style>" if css else ""
    return _DOCUMENT_WRAPPER.format(style_block=style_block, body=fragment)


def preview_html_document(html: str, css: str = "", page_size: dict[str, str] | None = None) -> str:
    """A complete, browser-viewable HTML document for an on-screen template preview (an iframe) —
    the same base + page CSS WeasyPrint applies, inlined into the ``<head>``, so the preview matches
    the PDF layout. Pure string assembly: no native stack, so the API can serve it directly. The
    template's Jinja tokens are left intact (this previews the layout, not a data-filled render)."""
    pw = (page_size or {}).get("width", "210mm")
    ph = (page_size or {}).get("height", "297mm")
    # Preview chrome goes last so it wins over the base CSS (which paints the page white/flush).
    style = _make_page_css(pw, ph) + _BASE_CSS_STATIC + "\n" + (css or "") + _PREVIEW_CHROME_CSS
    return _DOCUMENT_WRAPPER.format(style_block=f"<style>{style}</style>", body=html)


class WeasyPrintRenderer:
    """WeasyPrint (sync) wrapped in ``asyncio.to_thread`` so it doesn't block the event loop."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url

    def _render_sync(
        self,
        html: str,
        css: str,
        branding_css: str = "",
        page_size: dict[str, str] | None = None,
        overlay_css: str = "",
    ) -> bytes:
        # lazy: the native stack is only needed at render time (and has no type stubs).
        from weasyprint import CSS, HTML  # type: ignore[import-untyped]
        from weasyprint.text.fonts import FontConfiguration  # type: ignore[import-untyped]

        font_config = FontConfiguration()
        pw = (page_size or {}).get("width", "210mm")
        ph = (page_size or {}).get("height", "297mm")
        combined_css = (f":root {{ {branding_css} }}\n" if branding_css else "") + css
        full_html = _wrap_html(html, combined_css)
        base_css = _make_page_css(pw, ph) + _BASE_CSS_STATIC
        # Overlay is applied AFTER the template base sheet, so a bespoke document (the application
        # pack) can set its own page margin. Template CSS stays in the document and does not.
        sheets = [CSS(string=base_css, font_config=font_config)]
        if overlay_css.strip():
            sheets.append(CSS(string=overlay_css, font_config=font_config))
        doc = HTML(
            string=full_html, base_url=self._base_url, url_fetcher=_safe_url_fetcher()
        ).render(
            stylesheets=sheets,
            font_config=font_config,
        )
        return doc.write_pdf()  # type: ignore[no-any-return]

    async def to_pdf(
        self,
        html: str,
        css: str = "",
        branding_css: str = "",
        page_size: dict[str, str] | None = None,
        overlay_css: str = "",
    ) -> tuple[bytes, str]:
        pdf_bytes = await asyncio.to_thread(
            self._render_sync, html, css, branding_css, page_size, overlay_css
        )
        return pdf_bytes, sha256_of(pdf_bytes)


# ── Playwright / Chromium (opt-in) ────────────────────────────────────────────
class PlaywrightRenderer:
    """Headless-Chromium renderer (A4, zero margins, backgrounds on).

    Uses Playwright's **sync** API inside ``asyncio.to_thread`` — on Windows the async API needs the
    Proactor event loop, but the app runs the Selector loop (psycopg async requires it), so the two
    can't coexist; a worker thread has no loop and sidesteps the conflict. Launches a browser per
    render (fine for dev/generation volumes). Ignores ``page_size``/``branding_css`` — that handling
    is WeasyPrint-specific; Chromium paginates from the page's own CSS.

    SECURITY: this path has NO equivalent of ``_safe_url_fetcher`` — a real browser will fetch any
    ``http(s)``/``file`` URL in the document, so it must NOT be enabled for a caller-supplied-HTML
    render (e.g. the preview endpoint) without adding request interception that blocks internal /
    file targets first. Opt-in and currently unused (every template renders with WeasyPrint)."""

    def _render_sync(self, html: str, css: str) -> bytes:
        from playwright.sync_api import sync_playwright  # lazy

        full_html = _wrap_html(html, css)
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                page = browser.new_page()
                page.set_content(full_html, wait_until="networkidle")
                pdf_bytes: bytes = page.pdf(
                    format="A4",
                    print_background=True,
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
            finally:
                browser.close()
        return pdf_bytes

    async def to_pdf(
        self,
        html: str,
        css: str = "",
        branding_css: str = "",
        page_size: dict[str, str] | None = None,
        overlay_css: str = "",
    ) -> tuple[bytes, str]:
        pdf_bytes = await asyncio.to_thread(self._render_sync, html, f"{css}\n{overlay_css}")
        return pdf_bytes, sha256_of(pdf_bytes)


def weasyprint_available() -> tuple[bool, str]:
    """Whether WeasyPrint's native stack can actually load here, and why not if it cannot.

    WeasyPrint needs Pango/Cairo/GLib, which install cleanly on Linux and not on Windows — the
    reason the render worker is a Linux container in dev (`docker-compose.local.yaml`'s `render`
    service) while the Windows-native API only enqueues. The import is lazy inside
    `WeasyPrintRenderer`, so nothing notices until a job is already claimed: the native worker took
    a render job, failed on `cannot load library 'libgobject-2.0-0'`, burned one of the job's
    attempts, and — because both workers poll the same queue with SKIP LOCKED — took it from the
    container that could have rendered it. Three attempts of that dead-letters a document that was
    never un-renderable.

    So the worker asks this BEFORE starting its render loop and declines to claim what it cannot
    do. Probing the import is the honest test: a flag would have to be configured correctly on
    every machine, and the thing being tested is whether a library loads.
    """
    try:
        from weasyprint import HTML  # noqa: F401
        from weasyprint.text.fonts import FontConfiguration

        FontConfiguration()
    except Exception as exc:  # noqa: BLE001 — an OSError from ctypes, an ImportError, or worse
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def get_renderer(name: str | None) -> DocumentRenderer:
    """Renderer for a version's ``renderer`` value ('weasy' default, 'chromium' opt-in)."""
    if name == "chromium":
        return PlaywrightRenderer()
    return WeasyPrintRenderer()


async def render_pdf(
    html: str,
    css: str = "",
    branding_css: str = "",
    page_size: dict[str, str] | None = None,
    renderer: str | None = None,
    overlay_css: str = "",
) -> tuple[bytes, str]:
    """Render (html, css) → (pdf, sha256), delegating to the worker's render service when it's
    configured (``settings.render_service_url``) so a template preview runs on the SAME engine
    generation uses — the worker — and the API needs no native render stack. Falls back to an
    in-process render when no service URL is set (a deploy shipping WeasyPrint in the API image).

    Generation does NOT use this — it renders in the worker's ``render_jobs`` loop. This is the
    preview path; output is byte-identical because the service calls the same ``get_renderer``.
    """
    from app.config import settings  # local: keep this module import-light (tests mock to_pdf)

    if settings.render_service_url:
        import httpx

        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                settings.render_service_url.rstrip("/") + "/internal/render",
                json={
                    "html": html,
                    "css": css,
                    "branding_css": branding_css,
                    "page_size": page_size,
                    "renderer": renderer,
                    "overlay_css": overlay_css,
                },
            )
            resp.raise_for_status()
        return resp.content, sha256_of(resp.content)
    return await get_renderer(renderer).to_pdf(
        html, css, branding_css, page_size, overlay_css=overlay_css
    )
