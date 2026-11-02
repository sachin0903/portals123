"""The browser beacon is clamped, and a bad trace id emits nothing."""

from app.rum import identified, parse_rum


def test_a_malformed_trace_id_emits_nothing() -> None:
    assert (
        parse_rum(
            {
                "trace_id": "not-a-trace",
                "span_id": "0123456789abcdef",
                "route": "/accounts/[uid]",
                "portal": "client",
                "settle_ms": 12,
            }
        )
        is None
    )


def test_a_raw_url_is_not_a_route() -> None:
    assert (
        parse_rum(
            {
                "trace_id": "ab" * 16,
                "route": "/accounts/ada@x.com",
                "portal": "client",
                "settle_ms": 12,
            }
        )
        is None
    )


def test_vitals_are_clamped_and_missing_ones_are_omitted() -> None:
    view = parse_rum(
        {
            "trace_id": "ab" * 16,
            "span_id": "0123456789abcdef",
            "route": "/accounts/[uid]",
            "portal": "platform",
            "settle_ms": -4,
            "lcp_ms": 9_999_999,
            "cls": 0.02,
        }
    )
    assert view is not None
    assert view.attributes["rum.route"] == "/accounts/[uid]"
    assert view.attributes["rum.portal"] == "platform"
    assert view.attributes["rum.settle_ms"] == 0
    assert view.attributes["rum.lcp_ms"] == 600_000
    assert view.attributes["rum.cls"] == 0.02
    assert "rum.inp_ms" not in view.attributes
    assert "rum.ttfb_ms" not in view.attributes


def test_a_posted_user_id_is_not_a_span_attribute() -> None:
    view = parse_rum(
        {
            "trace_id": "ab" * 16,
            "route": "/accounts/[uid]",
            "portal": "platform",
            "settle_ms": 12,
            "enduser.id": "forged",
            "tenant.id": "9",
            "session.id": "forged-session",
        }
    )
    assert view is not None
    assert "enduser.id" not in view.attributes
    assert "tenant.id" not in view.attributes
    assert "session.id" not in view.attributes


def test_the_server_stamps_the_resolved_caller() -> None:
    view = parse_rum(
        {
            "trace_id": "ab" * 16,
            "route": "/accounts/[uid]",
            "portal": "platform",
            "settle_ms": 12,
        }
    )
    assert view is not None
    stamped = identified(
        view,
        user_id="11111111-1111-1111-1111-111111111111",
        tenant_id=4,
        session_id="sess_1",
    )
    assert stamped.attributes["enduser.id"] == "11111111-1111-1111-1111-111111111111"
    assert stamped.attributes["tenant.id"] == "4"
    assert stamped.attributes["session.id"] == "sess_1"
    assert stamped.attributes["rum.route"] == "/accounts/[uid]"
    assert "enduser.id" not in view.attributes


def test_a_missing_caller_adds_no_identity() -> None:
    view = parse_rum(
        {
            "trace_id": "ab" * 16,
            "route": "/",
            "portal": "client",
            "settle_ms": 1,
        }
    )
    assert view is not None
    stamped = identified(view, user_id=None, tenant_id=None, session_id=None)
    assert "enduser.id" not in stamped.attributes
    assert "tenant.id" not in stamped.attributes
    assert "session.id" not in stamped.attributes
    assert identified(view, user_id="", tenant_id=None, session_id="").attributes == view.attributes
