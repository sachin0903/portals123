"""Synchronous preview-render HTTP service, hosted by the DocumentWorker.

A preview is the SAME render as generation, on the SAME engine (``renderers.get_renderer``) — just
a different entry point. The API POSTs the sample-filled HTML here and gets the PDF back, so a
preview is byte-identical to the generated document, and the (possibly Windows) API never needs
WeasyPrint's native stack, which won't load there. Generation itself does NOT go through here — it
renders in the worker's ``render_jobs`` loop already; this endpoint is previews only.

Guards, because previews must stay second-class to client-facing generation:
  * a concurrency SEMAPHORE (``settings.render_service_concurrency``) so a burst of previews can't
    saturate the render thread pool the generation loop shares;
  * a content-hash CACHE (a bounded LRU) so re-opening a preview — and the editor↔overview
    double-render of the same content — doesn't re-run WeasyPrint;
  * a per-render TIMEOUT so one pathological template can't pin a slot forever.

Internal only — never expose this port publicly: it renders arbitrary posted HTML.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict

from fastapi import FastAPI, Response
from pydantic import BaseModel

from app.config import settings
from app.render.renderers import get_renderer

RENDER_TIMEOUT_SECONDS = 60.0
CACHE_MAX_ENTRIES = 64

# One semaphore + cache per worker process. The semaphore bounds concurrent renders; the cache is a
# simple LRU keyed on the render inputs' content hash.
_semaphore = asyncio.Semaphore(max(1, settings.render_service_concurrency))
_cache: OrderedDict[str, bytes] = OrderedDict()


class RenderIn(BaseModel):
    html: str
    css: str = ""
    branding_css: str = ""
    page_size: dict[str, str] | None = None
    renderer: str | None = None
    # Applied after the template base sheet, so a bespoke document can set its own page margin.
    overlay_css: str = ""


def _cache_key(body: RenderIn) -> str:
    payload = json.dumps(
        [body.html, body.css, body.branding_css, body.page_size, body.renderer, body.overlay_css],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> bytes | None:
    pdf = _cache.get(key)
    if pdf is not None:
        _cache.move_to_end(key)  # LRU: most-recently-used to the end
    return pdf


def _cache_put(key: str, pdf: bytes) -> None:
    _cache[key] = pdf
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)  # evict least-recently-used


app = FastAPI(title="render-service", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/internal/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/internal/render")
async def render(body: RenderIn) -> Response:
    """Render (html, css) → PDF via the same engine generation uses. Cache-first; a cache miss
    renders under the concurrency semaphore with a timeout."""
    key = _cache_key(body)
    cached = _cache_get(key)
    if cached is not None:
        return Response(content=cached, media_type="application/pdf")
    async with _semaphore:
        # A concurrent identical request may have filled the cache while we waited for a slot.
        cached = _cache_get(key)
        if cached is not None:
            return Response(content=cached, media_type="application/pdf")
        pdf, _sha = await asyncio.wait_for(
            get_renderer(body.renderer).to_pdf(
                body.html,
                body.css,
                body.branding_css,
                body.page_size,
                overlay_css=body.overlay_css,
            ),
            timeout=RENDER_TIMEOUT_SECONDS,
        )
    _cache_put(key, pdf)
    return Response(content=pdf, media_type="application/pdf")
