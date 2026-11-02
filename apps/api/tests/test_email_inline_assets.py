"""The firm's own images travel WITH the message, resolved from its bucket.

Every logo, icon and library image is stored as `/_assets/{name}` and the send path makes that
absolute against the firm's portal host. That is enough for a recipient who allows remote images
and not enough in general: most clients block a remote image until the reader asks for it, the
API may have no route to the public portal host, and on a dev box it has none over https — so the
masthead arrived as a broken-image glyph.

So the bytes go with the mail as a content id, read through `storage.get_public_asset` — the same
read the portal's relay performs, with no HTTP request to a URL that came out of author-supplied
markup. What these pin is the WIRE: `src="cid:img1"` plus an attachment, because that is what
decides whether a logo appears in an inbox, and it is invisible from the canvas.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from app.email import images as email_images

NAME = "logo-0123456789abcdef.png"
RELAY = f"/_assets/{NAME}"
ABSOLUTE = f"https://firm.example.test{RELAY}"
PNG = b"\x89PNG\r\n\x1a\nfake-but-bytes"


# ── Reading the names out of the markup ──────────────────────────────────────────────────────


def test_both_url_shapes_name_the_same_asset() -> None:
    """The path is what is matched, so the stored form and the send form both resolve. The host is
    deliberately not checked: the name is read from THIS tenant's bucket, so a src naming another
    host cannot reach another firm's asset — it resolves this firm's, or nothing."""
    assert email_images.relay_asset_name(RELAY) == NAME
    assert email_images.relay_asset_name(ABSOLUTE) == NAME
    assert email_images.relay_asset_name(f"http://portals2.localhost:5183{RELAY}") == NAME


def test_a_name_the_relay_would_refuse_is_not_an_asset() -> None:
    """The grammar is the relay's own. A name it will not serve must not become an attachment."""
    for src in ("/_assets/logo.png", "/_assets/../secret-0123456789abcdef.png", "/files/x.png"):
        assert email_images.relay_asset_name(src) is None, src


def test_the_names_are_read_in_order_and_deduped() -> None:
    html = f'<img src="{RELAY}"><img src="{ABSOLUTE}"><img src="/_assets/icon-{"a" * 16}.png">'
    assert email_images.relay_asset_names(html) == [NAME, f"icon-{'a' * 16}.png"]


def test_markup_with_no_images_reads_nothing() -> None:
    assert email_images.relay_asset_names("<p>Hello</p>") == []


# ── The wire ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_resolved_asset_becomes_a_content_id_and_an_attachment() -> None:
    """THE POINT OF ALL OF IT. What leaves the building is `cid:img1` and the bytes beside it —
    not a URL the recipient's client has to be willing to fetch."""
    html = f'<p>Hi</p><img src="{ABSOLUTE}" alt="Ridgeway">'
    out, attachments = await email_images.inline_own_images(
        html, own_assets={NAME: (PNG, "image/png")}
    )
    assert 'src="cid:img1"' in out
    assert ABSOLUTE not in out
    assert len(attachments) == 1
    assert attachments[0].content_id == "img1"
    assert attachments[0].filename == NAME
    assert base64.b64decode(attachments[0].content) == PNG
    # The payload Resend is handed, in its own field names, with the type stated.
    assert set(attachments[0].payload()) == {"filename", "content", "content_id", "content_type"}
    assert attachments[0].payload()["content_type"] == "image/png"


@pytest.mark.asyncio
async def test_one_asset_used_twice_travels_once() -> None:
    html = f'<img src="{RELAY}"><img src="{ABSOLUTE}">'
    out, attachments = await email_images.inline_own_images(
        html, own_assets={NAME: (PNG, "image/png")}
    )
    assert out.count('src="cid:img1"') == 2
    assert len(attachments) == 1


@pytest.mark.asyncio
async def test_an_unresolved_asset_is_left_as_its_url() -> None:
    """The floor: an asset the caller could not read keeps its URL, which still loads for a
    recipient who allows remote images. Never a broken `cid:` reference to nothing."""
    html = f'<img src="{ABSOLUTE}">'
    out, attachments = await email_images.inline_own_images(html, own_assets={})
    assert out == html
    assert attachments == []


@pytest.mark.asyncio
async def test_somebody_elses_image_is_never_attached() -> None:
    html = '<img src="https://cdn.example.test/their-logo.png">'
    out, attachments = await email_images.inline_own_images(
        html, own_assets={NAME: (PNG, "image/png")}
    )
    assert out == html and attachments == []


@pytest.mark.asyncio
async def test_the_budget_holds_and_the_rest_keep_their_urls() -> None:
    """Inlining grows the message, so it is bounded — and past the bound an image stays a URL
    rather than being dropped."""
    names = [f"logo-{i}{'0' * 15}.png" for i in range(email_images.MAX_INLINE_IMAGES + 2)]
    html = "".join(f'<img src="/_assets/{n}">' for n in names)
    out, attachments = await email_images.inline_own_images(
        html, own_assets=dict.fromkeys(names, (PNG, "image/png"))
    )
    assert len(attachments) == email_images.MAX_INLINE_IMAGES
    # The ones past the cap are untouched, not removed.
    for n in names[email_images.MAX_INLINE_IMAGES :]:
        assert f'src="/_assets/{n}"' in out


@pytest.mark.asyncio
async def test_an_asset_over_the_byte_budget_stays_a_url() -> None:
    big = b"x" * (email_images.MAX_INLINE_ONE_BYTES + 1)
    html = f'<img src="{RELAY}">'
    out, attachments = await email_images.inline_own_images(
        html, own_assets={NAME: (big, "image/png")}
    )
    assert out == html and attachments == []


@pytest.mark.asyncio
async def test_no_http_request_is_made_for_a_relay_asset() -> None:
    """The design claim, asserted rather than described: a relay asset is READ, never fetched. The
    client is handed in and must go untouched — if this ever starts fetching the portal, the API
    needs a route to it and the SSRF question comes back."""

    class _Forbidden:
        async def get(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not run
            raise AssertionError("the inliner made an HTTP request for a relay asset")

        async def aclose(self) -> None:
            return None

    out, attachments = await email_images.inline_own_images(
        f'<img src="{ABSOLUTE}">',
        client=_Forbidden(),  # type: ignore[arg-type]
        own_assets={NAME: (PNG, "image/png")},
    )
    assert 'src="cid:img1"' in out and len(attachments) == 1


# ── The send path's own resolution ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_tenant_resolves_nothing_rather_than_guessing() -> None:
    """`ctx` is optional on `send_email`, so a send with no tenant has no bucket to read. Its
    images stay URLs — never another firm's asset of the same name."""
    from app.email import transport

    assert await transport._own_assets(f'<img src="{RELAY}">', None) == {}  # noqa: SLF001


@pytest.mark.asyncio
async def test_a_storage_outage_leaves_the_images_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """An email must not fail because an image could not be read — the behaviour that shipped
    before any of this existed."""
    from app.email import transport

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("object store down")

    monkeypatch.setattr(transport.storage, "get_public_asset", _boom)
    assert await transport._own_assets(f'<img src="{RELAY}">', 1) == {}  # noqa: SLF001
