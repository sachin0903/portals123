"""Browser context a portal forwards with a successful sign-in.

WorkOS records IP and user agent only when the authenticate call sends them. The same facts
are stamped on ``authentication.succeeded`` so the trail still has them after the session
ends. Caps are the trust boundary: the caller is our own portal, but a huge header must not
land in the audit row.
"""

from __future__ import annotations


def device_label(user_agent: str | None) -> str | None:
    """ "Chrome on macOS". The same reading as ``deviceLabel`` in ``@graphene/auth``.

    WorkOS has no device-type field. Keep the two copies in step: the cases live in
    ``client-context.test.ts`` and ``test_sign_in_context.py``.
    """
    if not user_agent or not user_agent.strip():
        return None
    ua = user_agent
    browser: str | None = None
    if "Edg/" in ua:
        browser = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        browser = "Opera"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    os_name: str | None = None
    if any(token in ua for token in ("iPhone", "iPad", "iPod")):
        os_name = "iOS"
    elif "Android" in ua:
        os_name = "Android"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        os_name = "macOS"
    elif "Windows" in ua:
        os_name = "Windows"
    elif "CrOS" in ua:
        os_name = "ChromeOS"
    elif "Linux" in ua:
        os_name = "Linux"
    if browser and os_name:
        return f"{browser} on {os_name}"
    return browser or os_name


MAX_IP = 64
MAX_USER_AGENT = 256
MAX_DEVICE = 80


def _clean(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    cleaned = "".join(ch for ch in value if ch >= " " and ch != "\x7f").strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def sign_in_context(
    *, client_ip: str | None, user_agent: str | None, device: str | None
) -> dict[str, str]:
    """The audit fields worth keeping. Empty when the portal sent nothing usable."""
    out: dict[str, str] = {}
    ip = _clean(client_ip, MAX_IP)
    ua = _clean(user_agent, MAX_USER_AGENT)
    label = _clean(device, MAX_DEVICE)
    if ip:
        out["ip"] = ip
    if ua:
        out["user_agent"] = ua
    if label:
        out["device"] = label
    return out
