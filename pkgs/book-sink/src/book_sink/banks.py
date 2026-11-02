"""Fold SEI pay-to profiles into an account without dropping what the firm typed.

The nightly drop is the source for profiles SEI already holds. An entry the firm
typed — no SEI profile id, or a different provider — stays, in the order stored,
ahead of the profiles from the latest file. A profile that has left the file leaves
the account. An empty result is stored as SQL NULL so the card falls back to the
client.
"""

from __future__ import annotations

import json
from typing import Any


class UnreadableBankAccounts(ValueError):
    """The staged value was not a JSON list. The account is left alone."""


def merged_bank_accounts(existing: Any, incoming: Any) -> list[dict[str, Any]] | None:
    """Firm-typed entries, then the SEI pay-to profiles from this drop."""
    kept = [entry for entry in _entries(existing) if not _copied_sei_pay_to(entry)]
    added: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in _entries(incoming):
        if not _copied_sei_pay_to(entry):
            continue
        ref = str(entry.get("provider_ref") or "").strip()
        if ref in seen:
            continue
        seen.add(ref)
        added.append(entry)
    merged = kept + added
    return merged or None


def _entries(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "null":
            return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UnreadableBankAccounts("staged bank_accounts was not a list") from exc
    if isinstance(value, dict):
        one = _clean(value)
        return [one] if one else []
    if isinstance(value, list):
        rows: list[dict[str, Any]] = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            one = _clean(entry)
            if one:
                rows.append(one)
        return rows
    raise UnreadableBankAccounts("staged bank_accounts was not a list")


def _clean(entry: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, raw in entry.items():
        if raw is None:
            continue
        if isinstance(raw, str) and not raw.strip():
            continue
        out[str(key)] = raw.strip() if isinstance(raw, str) else raw
    return out


def _copied_sei_pay_to(entry: dict[str, Any]) -> bool:
    provider = str(entry.get("provider") or "").strip().lower()
    direction = str(entry.get("direction") or "").strip().lower()
    ref = str(entry.get("provider_ref") or "").strip()
    return provider == "sei" and direction == "pay_to" and bool(ref)
