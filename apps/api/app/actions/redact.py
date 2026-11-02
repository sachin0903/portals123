"""The redactor baton applies before it stores a run's params, a step's inputs and outputs, and
every attempt body. It is the same cut the old engine made on attempt bodies: the firm sees the
provider exchange, but not the ComplyCube AML band (``compliance.client_risk`` is not a firm
fact). Bank digits that live on a run's command payload are not surfaced in the listing by the
read side; here we only strip the AML rating."""

from __future__ import annotations

from typing import Any


def firm_visible(value: Any) -> Any:
    try:
        from app.integrations.sei.enrol import without_firm_aml

        return without_firm_aml(value)
    except Exception:  # noqa: BLE001 - redaction must never break a store write
        return value
