"""How a movement is NAMED on a client's activity feed.

The client portal builds this row twice — here, for ``/crm/home``'s recent slice, and in the
portal's own loader for the full ``/crm/transactions`` window — so the two have to agree or the
same payment reads one way on the home page and another on the activity page.

Both had the same defect: the title fell back to the raw key when a movement named no
instrument, which is EVERY payment, fee and dividend. So a payment in was titled ``deposit``
directly above its own subtitle saying "Payment" — one row, two words, for one thing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.routers.crm import (
    _ACTIVITY_LABELS,
    _activity_description,
    _activity_detail,
    _activity_type,
)

_TS = (
    Path(__file__).resolve().parents[3]
    / "pkgs"
    / "ui"
    / "src"
    / "lib"
    / "components"
    / "investment"
    / "activity.ts"
)


def test_a_cash_movement_is_titled_with_the_word_its_own_subtitle_uses() -> None:
    assert _activity_description({"type": "Deposit", "amount": 355}) == "Payment"
    assert _activity_description({"type": "Adviser fee", "amount": -406}) == "Fee"
    assert _activity_description({"type": "Dividend", "amount": 389}) == "Dividend"


def test_an_instrument_row_is_titled_by_the_instrument() -> None:
    assert _activity_description({"type": "Buy", "asset_name": "Fund A"}) == "Fund A"


def test_a_canonical_kind_is_named_not_bucketed_into_a_neighbour() -> None:
    # Interest fell through the needle search to 'buy', so a client read "+£0.01 Buy" against
    # their cash. A `platform.transaction_type` value is the enum and is named as itself.
    assert _activity_type({"movement_type": "interest", "type": "Asset Interest"}) == "interest"
    assert _activity_description({"movement_type": "interest", "sub_type": "20043"}) == "Interest"
    assert _activity_description({"type": "transfer_in"}) == "Transfer in"
    assert _activity_type({"movement_type": "transfer_out", "sub_type": "Internal"}) == (
        "transfer_out"
    )
    assert _activity_type({"type": "corporate_action"}) == "corporate_action"


def test_a_custodian_word_we_do_not_recognise_is_never_printed_raw() -> None:
    # An unknown custodian word still buckets rather than reaching the client as a key.
    assert _activity_description({"type": "Mystery movement"}) == "Buy"


def test_the_vocabulary_is_total_over_the_transaction_type_enum() -> None:
    # The enum in migration order. A kind added to the column without a word here is a kind
    # the feed would print as its neighbour.
    assert set(_ACTIVITY_LABELS) == {
        "buy",
        "sell",
        "dividend",
        "interest",
        "fee",
        "tax",
        "transfer_in",
        "transfer_out",
        "deposit",
        "withdrawal",
        "corporate_action",
        "adjustment",
    }


def test_a_movement_with_no_type_says_so_rather_than_guessing() -> None:
    assert _activity_description({"amount": 10}) == "Transaction"


def test_the_detail_is_the_custodians_words_when_they_add_something() -> None:
    assert _activity_detail({"movement_type": "fee", "sub_type": "Platform Fee"}) == (
        "Platform Fee"
    )
    assert _activity_detail({"movement_type": "deposit", "sub_type": "Employer"}) == "Employer"
    assert _activity_detail({"movement_type": "dividend", "sub_type": "Accumulation Dividend"}) == (
        "Accumulation Dividend"
    )


def test_the_detail_is_dropped_when_it_only_echoes_the_kind_or_is_a_code() -> None:
    assert _activity_detail({"movement_type": "deposit", "sub_type": "Deposit"}) is None
    assert _activity_detail({"movement_type": "deposit", "sub_type": "Payment"}) is None
    assert _activity_detail({"movement_type": "transfer_in", "sub_type": "transfer_in"}) is None
    assert _activity_detail({"movement_type": "interest", "sub_type": "20043"}) is None
    assert _activity_detail({"movement_type": "fee", "sub_type": ""}) is None
    assert _activity_detail({"movement_type": "fee"}) is None


@pytest.mark.pkgs_ui_pin
def test_the_two_languages_name_a_movement_identically() -> None:
    """The map here and ``ACTIVITY_LABELS`` in pkgs/ui are one vocabulary in two languages.

    The API container ships without pkgs/ui, so there is no import to make them one object —
    this reads the TypeScript source instead, which is the same posture ``test_branding.py``
    takes to ``block-contract.json``. A word changed on one side and not the other is a client
    reading two names for one movement depending on which page they are on.
    """
    body = _TS.read_text(encoding="utf-8")
    block = re.search(
        r"export const ACTIVITY_LABELS: Record<string, string> = \{(.*?)\};", body, re.S
    )
    assert block, f"ACTIVITY_LABELS not found in {_TS}"
    pairs = re.findall(r"(\w+):\s*'([^']*)'", block.group(1))
    assert dict(pairs) == _ACTIVITY_LABELS, (
        "the TypeScript and Python movement labels have drifted: "
        f"{json.dumps(dict(pairs), sort_keys=True)} vs "
        f"{json.dumps(_ACTIVITY_LABELS, sort_keys=True)}"
    )
