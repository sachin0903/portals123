"""Tipping-off guard on the notification tail (POCA 2002 s.333A).

The notifications consumer is a SECOND, independent tail of ``audit.actions`` (the audit READ
path's ``FIRM_EXCLUDED_EVENT_PREFIXES`` does not cover it), so an ``action.aml-*`` event must never
fan out to a firm-facing notification. Two pins: the catalogue offers no AML trigger
(allowlist-by-omission), AND ``handle_action`` refuses an AML event structurally — before it even
resolves rules — so a future AML-matching rule cannot breach it.
"""

import pytest

from app.notifications import catalogue, consumer


def test_catalogue_offers_no_aml_trigger() -> None:
    for trig in catalogue.CATALOGUE:
        for event_type in trig.event_types:
            assert not event_type.startswith("action.aml-"), (
                f"catalogue trigger {trig.key!r} offers AML event {event_type!r} — a firm must "
                "never be notified of screening activity (tipping-off)."
            )


@pytest.mark.asyncio
async def test_handle_action_refuses_aml_events_before_resolving_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def _spy(*_args: object, **_kwargs: object) -> list[object]:
        nonlocal called
        called = True
        return [object()]  # pretend a rule WOULD match this event

    monkeypatch.setattr(consumer.resolve, "effective_rules", _spy)
    # A sentinel session: if the guard fails to fire, handle_action would use it and raise.
    action = {"id": 1, "event_type": "aml.hit_decided"}
    await consumer.handle_action(object(), action, [])  # type: ignore[arg-type]
    assert not called, "the AML tipping-off guard did not fire before rule resolution"
