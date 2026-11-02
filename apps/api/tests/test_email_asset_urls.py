"""An email's images are REACHABLE — the masthead logo and an author's own pictures.

Every asset in this product is stored as a `/_assets/{name}` path and served by a same-origin relay
on the portal (`routers/internal.py::public_asset`): the browser never learns the object store's
endpoint, bucket or key. A path is meaningless in an inbox, though — it resolves against nothing,
and nothing about a preview reveals that, because the portal serves the path perfectly.

So the send path states a host. These tests pin the two halves of that:

  * the MASTHEAD, which falls back to the firm's name set in type when there is no reachable image
    (`shell.masthead`) — the placeholder this work exists to stop showing;
  * the BODY, where an author's uploaded logo lives if they placed one themselves.
"""

from __future__ import annotations

from typing import Any

import pytest

from app import storage
from app.email import document as email_document
from app.email import render as email_render
from app.email import shell as email_shell
from app.email import transport
from app.email.transport import Brand

LOGO_PATH = "/_assets/logo-0123456789abcdef.png"
ORIGIN = "https://ridgeway.example.test"


# ── The rewrite itself ───────────────────────────────────────────────────────────────────────


def test_a_stored_path_becomes_a_url_a_mail_client_can_fetch() -> None:
    body = f'<img src="{LOGO_PATH}" alt="Ridgeway">'
    assert storage.absolute_asset_urls(body, ORIGIN) == (
        f'<img src="{ORIGIN}{LOGO_PATH}" alt="Ridgeway">'
    )


def test_the_rewrite_is_idempotent() -> None:
    """`to_email_html` is the one seam every send passes through, and a caller may hand it HTML that
    has already been through a preview. A second pass must not double the origin."""
    once = storage.absolute_asset_urls(f'<img src="{LOGO_PATH}">', ORIGIN)
    assert storage.absolute_asset_urls(once, ORIGIN) == once


def test_nothing_else_in_the_document_is_touched() -> None:
    """Anchored on the quote, so only an attribute value is rewritten. A relative link, an absolute
    image on somebody else's host and the prefix mentioned in prose all survive untouched."""
    body = (
        '<a href="/accounts">Accounts</a>'
        '<img src="https://cdn.example.test/logo.png">'
        "<p>Assets live under /_assets/ on each portal.</p>"
    )
    assert storage.absolute_asset_urls(body, ORIGIN) == body


def test_a_name_the_relay_would_refuse_is_left_alone() -> None:
    """The grammar is the relay's own (`_PUBLIC_ASSET_NAME`): a name it will not serve must not be
    dressed up as a URL, because a 404 in an inbox is a broken picture and the path at least says
    what was meant."""
    body = '<img src="/_assets/logo.png"><img src="/_assets/../secret.png">'
    assert storage.absolute_asset_urls(body, ORIGIN) == body


def test_no_origin_leaves_the_document_as_authored() -> None:
    body = f'<img src="{LOGO_PATH}">'
    assert storage.absolute_asset_urls(body, "") == body


# ── The scheme: https everywhere except this machine ─────────────────────────────────────────


def test_a_real_host_is_stated_as_https() -> None:
    """Many mail clients refuse to load an http image, and a firm's logo arriving over plain http
    is worse than not arriving. A registered host that is not this machine is https."""
    from app.email.transport import _as_origin

    assert _as_origin("app.ridgeway.co.uk") == "https://app.ridgeway.co.uk"
    assert _as_origin("https://app.ridgeway.co.uk") == "https://app.ridgeway.co.uk"
    # An explicit scheme is honoured rather than doubled.
    assert _as_origin("http://legacy.example.test") == "http://legacy.example.test"
    assert _as_origin(None) is None and _as_origin("") is None


def test_a_dev_host_is_stated_as_http_so_the_logo_can_load() -> None:
    """The regression this closes: forcing https onto `portals2.localhost:5183`, where nothing
    listens on 443, turned the masthead from the firm's name in type — correct and readable — into
    a broken-image glyph with the alt text beside it. Worse than the fallback it replaced.

    Matched on the HOST, never the stage: `APP_STAGE` says what a deployment is FOR, and this asks
    only whether a browser can reach 443 here. `.localhost` is reserved for exactly this (RFC 6761).
    """
    from app.email.transport import _as_origin

    assert _as_origin("portals2.localhost:5183") == "http://portals2.localhost:5183"
    assert _as_origin("localhost:5185") == "http://localhost:5185"
    assert _as_origin("127.0.0.1:5183") == "http://127.0.0.1:5183"
    assert _as_origin("ridgeway.localhost") == "http://ridgeway.localhost"
    # Case is not a distinction a hostname makes.
    assert _as_origin("LOCALHOST:5183") == "http://LOCALHOST:5183"
    # …and a host that merely CONTAINS the word is not this machine.
    assert _as_origin("localhost.ridgeway.co.uk") == "https://localhost.ridgeway.co.uk"


# ── The masthead: type is the fallback, not the default ──────────────────────────────────────


def test_the_masthead_wears_the_logo_when_one_is_reachable() -> None:
    """The whole point: a firm with a logo gets its logo, not its name set in type."""
    out = email_shell.masthead(Brand(name="Ridgeway Wealth", logo_url=f"{ORIGIN}{LOGO_PATH}"))
    assert f'src="{ORIGIN}{LOGO_PATH}"' in out
    assert 'alt="Ridgeway Wealth"' in out


def test_the_masthead_falls_back_to_type_with_no_reachable_logo() -> None:
    """And the fallback is still there for a firm that has uploaded nothing — the name in the
    firm's own brand beats a broken image."""
    out = email_shell.masthead(Brand(name="Ridgeway Wealth"))
    assert "<img" not in out
    assert "Ridgeway Wealth" in out


# ── The seam every send passes through ───────────────────────────────────────────────────────


def test_the_send_document_carries_absolute_asset_urls() -> None:
    html = email_document.to_email_html(f'<img src="{LOGO_PATH}">', "", asset_origin=ORIGIN)
    assert f"{ORIGIN}{LOGO_PATH}" in html
    assert f'src="{LOGO_PATH}"' not in html


def test_the_preview_keeps_the_path_it_is_served_from() -> None:
    """The editor's preview is displayed inside the portal that stored the image, so the path
    resolves there. It is a SEND that has no origin of its own."""
    html = email_document.to_email_html(f'<img src="{LOGO_PATH}">', "", asset_origin=None)
    assert LOGO_PATH in html and ORIGIN not in html


def test_the_shipped_default_renders_the_logo_absolute_end_to_end() -> None:
    """`render_default` is the branch most firms are on — no published override — and it is the one
    that assembles the masthead from `brand.logo_url`. Driven whole: a brand carrying a resolved
    logo must produce an `<img>` pointing at the firm's own host, with no `/_assets/` path left in
    the document for a mail client to fail on.
    """
    brand = Brand(name="Ridgeway Wealth", logo_url=f"{ORIGIN}{LOGO_PATH}", accent="#0f766e")
    _subject, html = email_render.render_default(
        "invite",
        {
            "brand": transport.brand_tokens(brand),
            "first_name": "Max",
            "full_name": "Max Oakden",
            "surname": "Oakden",
            "set_password_url": "https://p.test/set",
        },
        asset_origin=ORIGIN,
    )
    assert f'src="{ORIGIN}{LOGO_PATH}"' in html
    assert 'src="/_assets/' not in html
    # The wordmark arm did NOT also render: the default picks one at merge time.
    assert html.count("Ridgeway Wealth") >= 1


@pytest.mark.asyncio
async def test_the_origin_is_resolved_from_either_of_the_firms_hosts() -> None:
    """`asset_origin` takes no portal, deliberately: the relay resolves the tenant from whichever
    host it was called on and reads that tenant's bucket, so both of a firm's hosts serve the same
    object. A staff email from a firm with only a client portal registered still gets a logo."""

    class _Session:
        def __init__(self, client: str | None, platform: str | None) -> None:
            self._row = {"client_portal_host": client, "platform_host": platform}

        async def execute(self, *_a: Any, **_k: Any) -> Any:
            row = self._row

            class _R:
                def mappings(self) -> Any:
                    class _M:
                        def one_or_none(self) -> dict[str, str | None]:
                            return row

                    return _M()

            return _R()

    both = _Session("client.test", "staff.test")
    assert await transport.asset_origin(both, 1) == "https://client.test"  # type: ignore[arg-type]

    platform_only = _Session(None, "staff.test")
    assert await transport.asset_origin(platform_only, 1) == "https://staff.test"  # type: ignore[arg-type]

    neither = _Session(None, None)
    assert await transport.asset_origin(neither, 1) is None  # type: ignore[arg-type]

    # No tenant at all (the ops/admin send) — nobody's assets to point at.
    assert await transport.asset_origin(both, None) is None  # type: ignore[arg-type]
