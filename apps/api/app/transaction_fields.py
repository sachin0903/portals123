"""What a transaction row shows beside its own currency.

The listing prints the movement in the currency it was booked in. When that is not the
account's currency, the second line is the same figure converted at the custodian's own
rate — the ratio of the base and local nets, or the file's exchange rate when the nets
are absent. It is not a fresh valuation and it is not the base net substituted under a
gross that was a different number: a buy whose face is the consideration and whose net
includes charges keeps the face, scaled by that rate.

The drawer also gets the custodian payload, with the blanks, the zeros and the
bookkeeping flags taken out. Those are not facts a reader can use.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from app import statement

_MONEY = Decimal("0.01")
_PRICE = Decimal("0.0001")


def present_transaction(row: dict[str, Any]) -> dict[str, Any]:
    """Attach ``base_amount``, ``base_currency`` and ``record``; drop the raw payload."""
    item = {key: value for key, value in row.items() if not str(key).startswith("__")}
    payload = _as_dict(item.pop("payload", None))
    currency = _code(item.get("currency"))
    base_currency = _code(item.get("base_currency")) or _code(
        payload.get("base_currency") or payload.get("ACCOUNT_BASE_CURRENCY")
    )
    item["base_currency"] = base_currency or None
    item["base_amount"] = base_amount(item.get("amount"), currency, base_currency, payload)
    item["price"] = _figure(item.get("price")) or unit_price(payload, item.get("quantity"))
    item["fee_amount"] = _figure(item.get("fee_amount")) or payload_money(
        payload, "net_charges_amount", "NET_FEE_AMT", "_s_fee"
    )
    item["tax_amount"] = _figure(item.get("tax_amount")) or payload_money(
        payload, "net_taxes_amount", "NET_TAXES_AMT", "_s_tax"
    )
    item["record"] = public_record(payload)
    if item.get("date") is not None:
        item["date"] = str(item["date"])
    if item.get("trade_date") is not None:
        item["trade_date"] = str(item["trade_date"])
    if item.get("transaction_date") is not None:
        item["transaction_date"] = str(item["transaction_date"])
    kind = item.get("type")
    item["family"] = statement.family(kind if isinstance(kind, str) else None)
    return item


def base_amount(
    amount: object,
    currency: str | None,
    base_currency: str | None,
    data: dict[str, Any] | None,
) -> float | None:
    """The displayed amount in the account currency, or None when there is nothing to add.

    None when the currencies match, the figure is zero, or the payload has no rate. A
    matching currency is already the base figure; a zero has no conversion worth a line.
    """
    figure = _decimal(amount)
    if figure is None or figure == 0 or not currency or not base_currency:
        return None
    if currency == base_currency:
        return None
    rate = fx_rate(data or {})
    if rate is None:
        return None
    return float((figure * rate).quantize(_MONEY))


def fx_rate(data: dict[str, Any]) -> Decimal | None:
    """The custodian's rate from this row, as base per one unit of the transaction currency."""
    rate = _ratio(data, "local_net_transaction_amount", "base_net_transaction_amount")
    if rate is not None:
        return rate
    rate = _ratio(
        data,
        "NET_TRANSACTION_AMOUNT",
        "BASE_TRANSACTION_AMOUNT",
        local_sign="_s_net",
        base_sign="_s_base",
    )
    if rate is not None:
        return rate
    explicit = _decimal(data.get("BASE_EXCHANGE_RATE"))
    if explicit is None or explicit == 0:
        return None
    return explicit


def unit_price(data: dict[str, Any], quantity: object = None) -> float | None:
    """The dealing price, or None when the row is not a trade.

    The file names it. The API does not, so it is the consideration divided by the units —
    the same quote the narrative prints after ``@``.
    """
    explicit = _decimal(data.get("UNIT_PRICE"))
    if explicit is not None and explicit != 0:
        return float(abs(explicit))
    gross = _decimal(data.get("local_transaction_gross_amount"))
    units = _decimal(data.get("transaction_quantity"))
    if units is None or units == 0:
        units = _decimal(quantity)
    if gross is None or gross == 0 or units is None or units == 0:
        return None
    return float(abs(gross / units).quantize(_PRICE))


def payload_money(data: dict[str, Any], api_key: str, file_key: str, sign_key: str) -> float | None:
    """A fee or a tax from the payload. The API figure is already signed; the file's is not."""
    api = _decimal(data.get(api_key))
    if api is not None and api != 0:
        return float(api.quantize(_MONEY))
    file_amount = _decimal(data.get(file_key))
    if file_amount is None or file_amount == 0:
        return None
    sign = _decimal(data.get(sign_key)) or Decimal(1)
    return float((file_amount * sign).quantize(_MONEY))


def public_record(data: dict[str, Any]) -> dict[str, str]:
    """Populated custodian fields, in the payload's own order.

    Underscore keys are pipeline internals. Debit/credit flags are the signs already
    applied to the amounts. A zero, a blank and a false flag are the absence of a fact.
    """
    out: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or key.startswith("_") or key.startswith("DEBIT_CREDIT_FLAG"):
            continue
        text = _present(value)
        if text is None:
            continue
        out[key] = text
    return out


def _ratio(
    data: dict[str, Any],
    local_key: str,
    base_key: str,
    *,
    local_sign: str | None = None,
    base_sign: str | None = None,
) -> Decimal | None:
    local = _signed(data, local_key, local_sign)
    base = _signed(data, base_key, base_sign)
    if local is None or base is None or local == 0:
        return None
    return base / local


def _signed(data: dict[str, Any], key: str, sign_key: str | None) -> Decimal | None:
    value = _decimal(data.get(key))
    if value is None or sign_key is None:
        return value
    sign = _decimal(data.get(sign_key))
    if sign is None:
        return value
    return value * sign


def _figure(value: object) -> float | None:
    """A stored money column, or None when it is blank or a zero placeholder."""
    number = _decimal(value)
    if number is None or number == 0:
        return None
    return float(number)


def _present(value: object) -> str | None:
    if isinstance(value, bool):
        return "Yes" if value else None
    number = _decimal(value)
    if number is not None:
        if number == 0:
            return None
        return format(number, "f")
    if value is None or isinstance(value, dict | list):
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None or isinstance(value, dict | list):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float | str):
        try:
            return Decimal(str(value).strip())
        except InvalidOperation:
            return None
    return None


def _code(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().upper()


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
