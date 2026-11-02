"""``sent_by_me`` — who a thread draws on which side, and why a broadcast must not carry it.

A thread puts your own messages on one side and everybody else's on the other, and "everybody else"
includes the firm's other advisers. ``sender_kind`` cannot answer that: it says staff or client, so
one adviser's reply was drawn as another adviser's own words, and — because the NAME followed the
same flag — labelled with the client's name.

Only the server can answer it: ``sender_user_id`` is an ``auth.users`` uuid and the portal holds a
WorkOS id, so the browser has nothing to compare against.

THE TRAP THIS PINS. The row handed back to the sender is the SAME object that gets broadcast to
every other adviser on the tenant. Delivered as-is, the flag is true for all of them, and each would
see that message drawn as their own. ``live_push.push`` strips it — at the chokepoint, not at
each call site, because a per-site rule is one a new call site can forget.
"""

from __future__ import annotations

import pytest

from app import live_push
from app.messaging import _shape_message


@pytest.mark.anyio
async def test_a_broadcast_never_claims_authorship() -> None:
    key = "tenant-test-authorship"
    subscriber = live_push.subscribe(key)
    try:
        await live_push.push(
            key,
            {
                "type": "message",
                "conversation_id": 1,
                # Exactly what the sender's own response carries.
                "message": {"id": 7, "body": "hello", "sender_kind": "staff", "sent_by_me": True},
            },
        )
    finally:
        live_push.unsubscribe(key, subscriber)

    payload = subscriber.queue.get_nowait()
    message = payload["message"]
    # Absent, not false: the portal reads a missing flag as "not mine", which is right for everyone
    # this payload can reach. The sender already has the message from their own response and
    # de-duplicates the echo by id.
    assert "sent_by_me" not in message
    # Everything else survives — the strip is surgical, not a rebuild.
    assert message["id"] == 7
    assert message["body"] == "hello"
    assert message["sender_kind"] == "staff"


@pytest.mark.anyio
async def test_a_payload_without_the_flag_passes_through_untouched() -> None:
    key = "tenant-test-authorship-2"
    subscriber = live_push.subscribe(key)
    try:
        await live_push.push(key, {"type": "typing", "conversation_id": 3})
    finally:
        live_push.unsubscribe(key, subscriber)
    assert subscriber.queue.get_nowait() == {"type": "typing", "conversation_id": 3}


def test_the_read_paths_ask_the_database_who_the_caller_is() -> None:
    """A static check, because the alternative is a lie that only shows up on screen.

    Each projection must derive the flag from ``auth.uid()`` rather than hard-coding it. There are
    three of them — the DM list, the insert's RETURNING, and the group feed's own projection — and
    the group one is a separate file that has already been missed once: without it a group message
    read as nobody's.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    expected = "COALESCE(m.sender_user_id = auth.uid(), false) AS sent_by_me"
    insert = "COALESCE(ins.sender_user_id = auth.uid(), false) AS sent_by_me"

    messaging = (root / "messaging.py").read_text(encoding="utf-8")
    groups = (root / "messaging_groups.py").read_text(encoding="utf-8")

    assert expected in messaging, "the DM list projection lost its authorship column"
    assert insert in messaging, "the insert RETURNING lost its authorship column"
    assert expected in groups, "the group feed projection lost its authorship column"
    # Never a constant: a hard-coded true is exactly the bug the broadcast strip exists to prevent.
    assert "'sent_by_me': True" not in messaging
    assert '"sent_by_me": True' not in messaging


def test_support_attribution_survives_message_shaping() -> None:
    shaped = _shape_message(
        {
            "id": 7,
            "conversation_id": 3,
            "conversation_uid": "conversation",
            "sender_kind": "staff",
            "sender_user_id": "real-operator-id",
            "sender_is_support": True,
            "sender_name": "Olivia Operator",
            "sender_role": None,
            "body": "I can help with that.",
            "created_at": "2026-09-10T00:00:00Z",
            "attachments": [],
            "request_item_uid": None,
            "request_item_title": None,
            "request_item_action": None,
            "request_item_status": None,
        }
    )
    assert shaped["sender_is_support"] is True
    assert shaped["sender_user_id"] == "real-operator-id"
    assert shaped["sender_name"] == "Olivia Operator"


def test_support_sender_name_is_snapshotted_past_cross_tenant_user_rls() -> None:
    """A target firm's client cannot SELECT the operations user's auth.users row.

    The name therefore has to be captured by the service-role send and preferred by every read
    projection; merely joining auth.users would render the support author as anonymous.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    messaging = (root / "app" / "messaging.py").read_text(encoding="utf-8")
    groups = (root / "app" / "messaging_groups.py").read_text(encoding="utf-8")
    migration = (root / "migrations" / "sql" / "0128_messaging_support_sender.sql").read_text(
        encoding="utf-8"
    )

    assert "sender_display_name text" in migration
    assert "(SELECT name FROM auth.users WHERE id = :sender_user_id)" in messaging
    assert "COALESCE(m.sender_display_name, u.name) AS sender_name" in messaging
    assert "COALESCE(m.sender_display_name, u.name) AS sender_name" in groups
