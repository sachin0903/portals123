"""Every link a notification can carry must name a real route in the portal it is delivered to.

This is the third time the same bug has shipped. `/adviser/messages` names no route and sent every
"new message from your adviser" notice to a 404; `/account` did the same for "your bank details were
updated"; and a serial-id deep link (`/clients/8`) resolves to nothing on a uid-addressed route. The
link is STORED ON THE ROW, so each one 404s for ever -- fixing the catalogue afterwards leaves the
rows already written pointing at nothing.

Nothing could catch it: the portals' own `route-addressing.test.ts` scans hrefs in SOURCE, and these
links are built in Python. So this walks the catalogue's links against the portals' route trees,
which is the same cross-language pin `pkgs_ui_pin` tests make for the block and flow contracts.

Marked `pkgs_ui_pin` for the same reason those are: the API container ships without the portals, so
this test is the only thing holding the two sides together, and it must be skippable where that
source is absent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.notifications import catalogue

REPO = Path(__file__).resolve().parents[3]
PORTAL_ROUTES = {
    "client": REPO / "apps/client/src/routes",
    "platform": REPO / "apps/platform/src/routes",
}

#: A `{token}` segment stands for a uid the sender fills in, so it matches any dynamic segment.
TOKEN = re.compile(r"^\{[^}]+\}$")


def _resolves(portal: str, link: str) -> bool:
    """Does `link` name a page in `portal`'s route tree?"""
    root = PORTAL_ROUTES[portal]
    node = root
    path = link.split("?", 1)[0]
    for seg in [s for s in path.strip("/").split("/") if s]:
        if TOKEN.match(seg):
            dynamic = [d for d in node.iterdir() if d.is_dir() and d.name.startswith("[")]
            if not dynamic:
                return False
            node = dynamic[0]
            continue
        nxt = node / seg
        if not nxt.is_dir():
            return False
        node = nxt
    return (node / "+page.svelte").exists()


def _links() -> list[tuple[str, str, str]]:
    """(where it came from, portal, link) for everything the catalogue can emit.

    `CATALOGUE` is the built list -- the built-ins plus the offered triggers, whose client
    audiences take their link from `_CLIENT_DEFAULTS` -- so walking it covers every link a
    delivered notification can carry."""
    out: list[tuple[str, str, str]] = []
    for trig in catalogue.CATALOGUE:
        for aud in trig.audiences:
            if aud.default_link:
                out.append(
                    (f"{trig.key}/{aud.key}", catalogue.audience_portal(aud.key), aud.default_link)
                )
    assert out, "the catalogue produced no links at all -- this test would pass vacuously"
    return out


@pytest.mark.pkgs_ui_pin
@pytest.mark.skipif(
    not PORTAL_ROUTES["client"].is_dir(), reason="portal sources absent (API container)"
)
def test_every_notification_link_names_a_real_route() -> None:
    dead = [
        f"  {where}: {portal} portal has no route for {link!r}"
        for where, portal, link in _links()
        if not _resolves(portal, link)
    ]
    assert not dead, (
        "notification links that would 404 (the link is stored on the row, so every one of these "
        "strands a reader for ever):\n" + "\n".join(dead)
    )


@pytest.mark.pkgs_ui_pin
@pytest.mark.skipif(
    not PORTAL_ROUTES["client"].is_dir(), reason="portal sources absent (API container)"
)
def test_no_notification_link_carries_a_serial_id() -> None:
    """A portal URL never carries a database key (docs/api.md, Addressing). `_click_link` resolves
    the public uid, and a serial there lands on a record that does not exist."""
    offenders = [
        f"  {where}: {link!r}" for where, _portal, link in _links() if re.search(r"/\d+(/|$)", link)
    ]
    assert not offenders, "notification links carrying a serial id:\n" + "\n".join(offenders)
