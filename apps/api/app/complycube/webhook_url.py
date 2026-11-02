"""A firm's webhook URL: what may be saved, and how a delivery reaches it.

The worker sends to an address a firm typed in, so the URL is a request Graphene makes into
whatever network it names. Three rules keep that request on the firm's public endpoint:

* HTTPS only, to a host name or a public IP, with no credentials in the URL (``check_syntax``).
  The firm API's ``PUT /webhook`` and the admin's webhook form check this when the URL is saved;
  the worker checks it again before every send.
* Every address the host resolves to must be public (``is_public``): not private, loopback,
  link-local, multicast, reserved, unspecified or shared (carrier-grade NAT), and not an IPv6 form
  that carries one of those (IPv4-mapped, 6to4, Teredo, NAT64). One non-public answer refuses the
  host.
* A send resolves the host once and connects to that address (``PinnedTransport``), keeping the
  host name for the ``Host`` header, the TLS server name and the certificate check. A name that is
  public when it is checked and private when it is connected to (DNS rebinding) cannot move the
  request, because nothing resolves it a second time.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable

import httpx

MAX_URL_LENGTH = 2048
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
# NAT64 prefixes translate to the IPv4 address in their last 32 bits.
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
#: ``(host, port)`` to the addresses it resolves to, as strings.
Resolver = Callable[[str, int], Awaitable[list[str]]]


class UnsafeWebhookUrl(ValueError):
    """The URL is not one Graphene will send to. The message is safe to show the firm."""


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address.teredo is not None:
        return address.teredo[1]
    for prefix in _NAT64:
        if address in prefix:
            return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def is_public(address: IPAddress) -> bool:
    """True when ``address`` is a globally routable unicast address."""
    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(address)
        if embedded is not None and not is_public(embedded):
            return False
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _ip_literal(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def check_syntax(url: str) -> httpx.URL:
    """Parse ``url`` and refuse what can never be sent to. Does not resolve the host."""
    if not url or len(url) > MAX_URL_LENGTH:
        raise UnsafeWebhookUrl(f"The webhook URL must be 1 to {MAX_URL_LENGTH} characters.")
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise UnsafeWebhookUrl("The webhook URL is not a valid URL.") from exc
    if parsed.scheme != "https":
        raise UnsafeWebhookUrl("The webhook URL must use HTTPS.")
    host = parsed.host.lower().rstrip(".")
    if not host:
        raise UnsafeWebhookUrl("The webhook URL must name a host.")
    if parsed.userinfo:
        raise UnsafeWebhookUrl("The webhook URL must not contain a user name or password.")
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        raise UnsafeWebhookUrl("The webhook URL must name a public host.")
    literal = _ip_literal(host)
    if literal is not None and not is_public(literal):
        raise UnsafeWebhookUrl("The webhook URL must resolve to a public address.")
    return parsed


async def system_resolver(host: str, port: int) -> list[str]:
    """The addresses ``host`` resolves to, from the operating system's resolver."""
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return [str(info[4][0]) for info in infos]


async def resolve_public(host: str, port: int, *, resolver: Resolver | None = None) -> list[str]:
    """Every address ``host`` resolves to, refusing the host when any of them is not public."""
    literal = _ip_literal(host)
    if literal is not None:
        if not is_public(literal):
            raise UnsafeWebhookUrl("The webhook URL must resolve to a public address.")
        return [str(literal)]
    try:
        found = await (resolver or system_resolver)(host, port)
    except (OSError, UnicodeError) as exc:
        raise UnsafeWebhookUrl("The webhook host could not be resolved.") from exc
    addresses: list[str] = []
    for raw in found:
        parsed = _ip_literal(raw.split("%", 1)[0])
        if parsed is None or not is_public(parsed):
            raise UnsafeWebhookUrl("The webhook URL must resolve to a public address.")
        if str(parsed) not in addresses:
            addresses.append(str(parsed))
    if not addresses:
        raise UnsafeWebhookUrl("The webhook host could not be resolved.")
    return addresses


async def check_url(url: str, *, resolver: Resolver | None = None) -> str:
    """``check_syntax`` and ``resolve_public`` together: the check a save runs."""
    parsed = check_syntax(url)
    await resolve_public(parsed.host, parsed.port or 443, resolver=resolver)
    return str(parsed)


class PinnedTransport(httpx.AsyncBaseTransport):
    """Send every request to one IP address, keeping the URL's host name for HTTP and TLS.

    The request URL's host is swapped for ``address`` just before the connection is opened.
    ``Host`` was already set from the original URL when the request was built, and the
    ``sni_hostname`` extension makes the TLS handshake name, and verify the certificate for, the
    host name rather than the address.
    """

    def __init__(self, address: str, *, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._address = address
        self._inner = inner or httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        request.url = request.url.copy_with(host=self._address)
        request.extensions = {**request.extensions, "sni_hostname": host}
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()
