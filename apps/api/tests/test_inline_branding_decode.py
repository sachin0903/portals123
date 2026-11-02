"""The one piece of `scripts/restore_inline_branding.py` that can be wrong quietly.

The script's job is to take a branding image that is stored as a base64 `data:` URI in
`auth.tenants.theme` and put it in the tenant's bucket instead — because an inline image renders in
the portals, is refused by `email/transport.py::_https_only` (mail clients block `data:`), and is
served on the branding hot path at its full size. A real deployment held a 265 kB logo that way.

Everything else in that script is a loop, a print and two calls the product already makes. The
decode is the part that could accept something it should not, or mangle bytes, without anyone
noticing until a firm's logo arrived corrupt — so it is a pure function and it is pinned here.
"""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "restore_inline_branding",
    Path(__file__).resolve().parents[1] / "scripts" / "restore_inline_branding.py",
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
decode_data_uri = _MODULE.decode_data_uri

PNG = bytes.fromhex("89504e470d0a1a0a") + b"a fake body \x00\xff\x10"


def test_a_stored_upload_round_trips_byte_for_byte() -> None:
    """The shape `put_branding_asset` writes on its last rung, read back."""
    uri = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
    decoded = decode_data_uri(uri)
    assert decoded is not None
    data, content_type = decoded
    assert data == PNG
    assert content_type == "image/png"


@pytest.mark.parametrize(
    "content_type",
    ["image/png", "image/jpeg", "image/svg+xml", "image/webp", "image/x-icon"],
)
def test_every_type_branding_accepts_survives_the_header_split(content_type: str) -> None:
    # `image/svg+xml` is the one that would break a naive `split(';')[0].split('/')[1]`, and
    # `image/x-icon` the one that would break a hardcoded set.
    uri = f"data:{content_type};base64," + base64.b64encode(PNG).decode("ascii")
    decoded = decode_data_uri(uri)
    assert decoded is not None
    assert decoded[1] == content_type


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/_assets/logo-0123456789abcdef.png",  # already stored — the common case, and NOT a rewrite
        "https://cdn.example.test/logo.png",
        "data:image/png,notbase64",  # a percent-encoded data URI: valid, and not what we write
        "data:;base64,AAAA",  # no content type
        "data:image/png;base64,",  # no payload
        "data:image/png;base64,!!!!",  # not base64 at all
        "data:image/png;base64,AAA",  # length 3: not a valid base64 quantum
    ],
)
def test_anything_that_is_not_one_of_ours_is_left_alone(value: str) -> None:
    """Returning None is what makes the script skip the field rather than rewrite it. A false
    accept here would replace a working hosted URL with the result of decoding nonsense."""
    assert decode_data_uri(value) is None


relay_path_for = _MODULE.relay_path_for

#: What a tenant provisioned inside docker compose actually holds (measured on the dev deployment).
BASE = "http://rustfs:9000/t-graphene"
HERO = "files/tenant/content-xmDhOYXI1hTj-hero-0123456789abcdef.png"


class TestTheAbsoluteStoreUrl:
    """The second shape, and on a container deployment the worse one.

    Before every upload path passed `proxy=True`, the store returned `{public_base_url}/{key}` —
    and `public_base_url` on a compose deployment is the INTERNAL service name. The image is then
    broken in the browser (nothing outside the compose network resolves `rustfs`), refused by email
    (not https), and the object store's own address is published into markup.
    """

    def test_a_store_url_becomes_the_relay_path_for_the_same_object(self) -> None:
        assert (
            relay_path_for(f"{BASE}/{HERO}", BASE)
            == "/_assets/content-xmDhOYXI1hTj-hero-0123456789abcdef.png"
        )

    def test_a_trailing_slash_on_the_base_is_tolerated(self) -> None:
        assert relay_path_for(f"{BASE}/{HERO}", BASE + "/") is not None

    @pytest.mark.parametrize(
        ("value", "base", "why"),
        [
            ("/_assets/logo-0123456789abcdef.png", BASE, "already a relay path"),
            (f"{BASE}/{HERO}", None, "no public base to match against"),
            (f"{BASE}/{HERO}", "", "no public base to match against"),
            ("https://cdn.example.test/files/tenant/x.png", BASE, "another host entirely"),
            (f"{BASE}/documents/private/x.pdf", BASE, "not under the public prefix"),
            (f"{BASE}/files/tenant/not-the-grammar.png", BASE, "the relay would refuse the name"),
            (f"{BASE}/files/tenant/logo-短-0123456789abcdef.png", BASE, "non-ASCII name"),
        ],
    )
    def test_anything_else_is_left_exactly_as_it_is(self, value: str, base: str, why: str) -> None:
        assert relay_path_for(value, base) is None, why

    def test_the_two_shapes_do_not_overlap(self) -> None:
        """A data URI is never an absolute store URL and vice versa, so the caller's order of
        checks cannot matter — which is what keeps the migration explainable."""
        uri = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
        assert relay_path_for(uri, BASE) is None
        assert decode_data_uri(f"{BASE}/{HERO}") is None


def test_whitespace_inside_the_payload_is_refused_rather_than_repaired() -> None:
    """`validate=True` is deliberate: base64 with embedded newlines is not what this estate writes,
    so accepting it would mean guessing about a value some other tool produced."""
    payload = base64.b64encode(PNG).decode("ascii")
    assert decode_data_uri(f"data:image/png;base64,{payload[:4]}\n{payload[4:]}") is None
