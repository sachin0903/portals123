"""An author's uploaded image must ACTUALLY ARRIVE — `app/email/images.py`.

The picture was stored, shown in the canvas and previewed correctly, then arrived broken, because
whether a recipient sees it depended on their mail client reaching our bucket. Document templates
already answer this by resolving their own resources server-side (`render/renderers.py`); the mail
equivalent is a content-id attachment, so the server fetches the bytes and the recipient needs no
access to our storage at all.

The rule that carries the safety argument is the ORIGIN ALLOWLIST: template HTML is author-supplied,
so anything but our own configured storage origin is left exactly as authored. These tests exist
mostly to hold that line.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from app.email import images as email_images

OURS = "http://storage.internal:9000"
THEIRS = "https://cdn.example.com"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _settings(monkeypatch: pytest.MonkeyPatch, endpoint: str = OURS, legacy: str = "") -> None:
    monkeypatch.setattr(email_images.settings, "s3_endpoint_url", endpoint)
    monkeypatch.setattr(email_images.settings, "s3_public_base_url", legacy)


def _client(routes: dict[str, tuple[int, bytes, str]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        hit = routes.get(str(request.url))
        if hit is None:
            return httpx.Response(404)
        status, body, ctype = hit
        return httpx.Response(status, content=body, headers={"content-type": ctype})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_our_own_image_travels_with_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    url = f"{OURS}/tnt-abc/files/tenant/image-1.png"
    html = f'<p>hi</p><img src="{url}" alt="logo">'
    async with _client({url: (200, PNG, "image/png")}) as client:
        out, attachments = await email_images.inline_own_images(html, client=client)
    assert 'src="cid:img1"' in out
    assert url not in out
    assert len(attachments) == 1
    a = attachments[0]
    assert a.content_id == "img1"
    assert a.filename == "image-1.png"
    assert base64.b64decode(a.content) == PNG
    # Resend's own field names, snake_case — and the type stated, never left to a guess.
    assert set(a.payload()) == {"filename", "content", "content_id", "content_type"}
    assert a.content_type == "image/png"


@pytest.mark.asyncio
async def test_ANOTHER_HOST_IS_NEVER_FETCHED(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE SAFETY LINE. Template HTML is author-supplied, so a general fetch would be the SSRF hole
    `_safe_url_fetcher` exists to close. Only our own origin is ever requested."""
    _settings(monkeypatch)
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    html = (
        f'<img src="{THEIRS}/logo.png">'
        '<img src="http://169.254.169.254/latest/meta-data/">'
        '<img src="file:///etc/passwd">'
        '<img src="{{ brand.logo_url }}">'
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out, attachments = await email_images.inline_own_images(html, client=client)
    assert asked == []
    assert attachments == []
    assert out == html


@pytest.mark.asyncio
async def test_a_failure_leaves_the_url_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The floor is the behaviour we had before this existed — never a blocked send."""
    _settings(monkeypatch)
    missing = f"{OURS}/tnt-abc/files/tenant/gone.png"
    not_an_image = f"{OURS}/tnt-abc/files/tenant/page.png"
    html = f'<img src="{missing}"><img src="{not_an_image}">'
    async with _client(
        {missing: (404, b"", "text/plain"), not_an_image: (200, b"<html>", "text/html")}
    ) as client:
        out, attachments = await email_images.inline_own_images(html, client=client)
    assert attachments == []
    assert out == html


@pytest.mark.asyncio
async def test_one_image_used_twice_travels_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    url = f"{OURS}/tnt-abc/files/tenant/image-1.png"
    fetches: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetches.append(str(request.url))
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    html = f'<img src="{url}"><p>x</p><img src="{url}">'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out, attachments = await email_images.inline_own_images(html, client=client)
    assert len(fetches) == 1
    assert len(attachments) == 1
    assert out.count('src="cid:img1"') == 2


@pytest.mark.asyncio
async def test_the_budget_leaves_the_rest_as_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bounded, because inlining grows the message — and the overflow keeps its URL rather than
    being dropped: a remote image that might load beats no image at all."""
    _settings(monkeypatch)
    monkeypatch.setattr(email_images, "MAX_INLINE_IMAGES", 2)
    urls = [f"{OURS}/tnt-abc/files/tenant/i{n}.png" for n in range(4)]
    routes = dict.fromkeys(urls, (200, PNG, "image/png"))
    html = "".join(f'<img src="{u}">' for u in urls)
    async with _client(routes) as client:
        out, attachments = await email_images.inline_own_images(html, client=client)
    assert len(attachments) == 2
    assert out.count("cid:") == 2
    for u in urls[2:]:
        assert u in out


@pytest.mark.asyncio
async def test_an_oversize_image_is_not_inlined(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch)
    monkeypatch.setattr(email_images, "MAX_INLINE_ONE_BYTES", 10)
    url = f"{OURS}/tnt-abc/files/tenant/big.png"
    html = f'<img src="{url}">'
    async with _client({url: (200, PNG, "image/png")}) as client:
        out, attachments = await email_images.inline_own_images(html, client=client)
    assert attachments == []
    assert url in out


@pytest.mark.asyncio
async def test_no_storage_configured_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, endpoint="", legacy="")
    html = '<img src="http://anywhere/x.png">'
    out, attachments = await email_images.inline_own_images(html)
    assert (out, attachments) == (html, [])


def test_the_legacy_shared_bucket_counts_as_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    _settings(monkeypatch, endpoint=OURS, legacy="https://assets.example.com/branding")
    assert email_images.allowed_origins() == (OURS, "https://assets.example.com")


def test_the_redaction_drops_the_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """A signed URL carries its credential in the query, and this string is logged."""
    assert email_images._redact("https://h/p/o.png?X-Amz-Signature=secret") == "https://h/p/o.png"


def test_send_email_is_the_one_place_this_happens() -> None:
    """The chokepoint argument: one call site, so no send site can forget it.

    The assertion is about the two things that must not come apart — the inlining happens in
    `send_email`, and what is POSTED is the INLINED body rather than the author's original. It
    used to read `'"html": body_html'` straight out of `send_email`, which broke the moment the
    payload moved into `_wire_payload`: a true refactor failed a test whose subject it had not
    changed. So it asks the question at the seam instead — `send_email` hands the inlined body to
    the builder, and the builder is the only thing that names the wire keys.
    """
    import inspect

    from app.email import transport as email_mod

    src = inspect.getsource(email_mod.send_email)
    assert "await email_images.inline_own_images(" in src
    # …and the firm's own assets are RESOLVED FIRST and handed in. Without this the inliner has
    # nothing to inline for a relay-stored image — every logo and library picture in the product —
    # and it silently travels as a URL the recipient's client has to be willing to fetch.
    assert "await _own_assets(" in src
    assert "own_assets=own" in src
    # The INLINED body reaches the payload builder — not `html`, which still holds `cid:`-free
    # markup that would arrive with broken images.
    assert "html=body_html" in src
    assert "attachments=" in src

    builder = inspect.getsource(email_mod._wire_payload)
    assert '"html": html' in builder
    assert 'payload["attachments"]' in builder
