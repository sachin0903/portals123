"""Unit tests for the outbox deliverer's pure decisions (backoff, dead-letter, payload)."""

import inspect
from pathlib import Path

from app.flows.notifications import (
    next_retry_seconds,
    should_dead_letter,
)


def test_flows_outbox_has_exactly_one_drain() -> None:
    """``flows.outbox`` must be claimed in ONE place: ``app/flows/dispatcher.py``.

    ``notifications.deliver_due`` was a second, uncalled drain of the same queue that dead-lettered
    with a generic 'delivery failed'. Two loops over one queue race each other and destroy the error
    provenance an operator needs, so the duplicate is deleted — and this fails if one comes back.
    """
    from app.flows import notifications

    root = Path(inspect.getfile(notifications)).parent.parent  # app/
    claimers = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        # A drain SELECTs from the queue AND claims rows; a prose mention of either is not one.
        if "FROM flows.outbox" in (src := p.read_text(encoding="utf-8"))
        and "FOR UPDATE SKIP LOCKED" in src
    )
    assert claimers == ["flows/dispatcher.py"], f"more than one flows.outbox drain: {claimers}"


def test_backoff_is_exponential_and_capped() -> None:
    assert next_retry_seconds(0) == 60
    assert next_retry_seconds(1) == 120
    assert next_retry_seconds(2) == 240
    assert next_retry_seconds(5) == 1920
    assert next_retry_seconds(20) == 3600  # capped at 1h
    assert next_retry_seconds(-3) == 60  # defensive on negative


def test_dead_letter_after_max_attempts() -> None:
    assert not should_dead_letter(7, 8)
    assert should_dead_letter(8, 8)
    assert should_dead_letter(9, 8)
