"""The browser context stored on a successful sign-in."""

from app.sign_in_context import device_label, sign_in_context


def test_device_label_names_browser_and_operating_system() -> None:
    chrome_mac = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    edge_win = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    )
    assert device_label(chrome_mac) == "Chrome on macOS"
    assert device_label(edge_win) == "Edge on Windows"
    assert device_label(None) is None
    assert device_label("   ") is None
    assert sign_in_context(client_ip="  ", user_agent=None, device="") == {}


def test_values_are_trimmed_and_capped() -> None:
    huge = "A" * 400
    got = sign_in_context(client_ip=" 203.0.113.8\n", user_agent=huge, device=" Chrome on macOS ")
    assert got["ip"] == "203.0.113.8"
    assert len(got["user_agent"]) == 256
    assert got["device"] == "Chrome on macOS"
