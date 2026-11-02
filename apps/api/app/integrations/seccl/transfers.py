"""Place a product transfer at Seccl, read its progress, and request a cancellation.

Seccl is both the scheme administrator and the custodian on this path, so one instruction
covers what Morgan Lloyd and SEI split between them. ``completed`` is still ours: Seccl's
``Completed`` means the assets are booked, which is ``funds_sent`` until we reconcile.

``isPensionTransfer`` is Seccl's name for safeguarded rights (Pension Schemes Act 2015
s.48(8)), not "this is a pension". Sending it because ``transfer_type`` is pension would
tell Seccl a safeguarded transfer was advised when it was not, and then demand an FCA
reference we do not have.
"""

from __future__ import annotations

from typing import Any

from app.integrations.seccl.connection import SecclNotConfigured, configured, seccl_settings

#: Crystallisation → ``productDrawdownStatus``. Postman's pension transfer lists three members:
#: No Drawdown is the uncrystallised portion, Partial Drawdown the crystallised portion only,
#: Full Drawdown both. A pension with no crystallisation on the row is refused, not guessed.
DRAWDOWN_FOR: dict[str, str] = {
    "uncrystallised": "No Drawdown",
    "crystallised": "Partial Drawdown",
    "partial": "Full Drawdown",
}


class SecclUnavailable(RuntimeError):
    """Seccl refused the call. The message is safe to show an adviser."""


class TransferNotCancellable(RuntimeError):
    """Seccl will not take a cancellation request in the transfer's current status."""


def _stated(transfer: dict[str, Any], key: str) -> str:
    data = transfer.get("data")
    if not isinstance(data, dict):
        return ""
    value = data.get(key)
    if value is None:
        return ""
    return str(value).strip()


def missing_to_place(transfer: dict[str, Any]) -> list[str]:
    """What a Product Transfer needs and this row has not got. Named, never defaulted."""
    absent: list[str] = []
    if not transfer.get("seccl_account_id"):
        absent.append("the account's Seccl id (links, provider 'seccl')")
    if not transfer.get("masterparty_id"):
        absent.append(
            "the ceding provider's Seccl masterparty id "
            "(platform.transfer_providers.masterparty_id)"
        )
    if (
        not _stated(transfer, "provider_address_line1")
        or not _stated(transfer, "provider_postcode")
        or not _stated(transfer, "provider_country")
    ):
        absent.append("the ceding scheme's address (line 1, postcode and country)")
    if transfer.get("total_value") is None:
        absent.append("a transfer value")
    if str(transfer.get("transfer_type") or "") == "pension":
        crystallisation = str(transfer.get("crystallisation") or "").strip().lower()
        if crystallisation not in DRAWDOWN_FOR:
            absent.append("whether the pension is crystallised, partial, or uncrystallised")
        if transfer.get("safeguarded_benefits") and not _stated(transfer, "fca_reference"):
            absent.append(
                "the advising firm's FCA reference (data.fca_reference) — Seccl requires it "
                "when the pension has safeguarded rights"
            )
    return absent


def build_product_transfer(transfer: dict[str, Any]) -> dict[str, Any]:
    """The ``transactions[]`` member. The group wrapper adds firmId and accountId."""
    absent = missing_to_place(transfer)
    if absent:
        raise ValueError(
            "Seccl requires these details before a transfer can be placed: " + ", ".join(absent)
        )
    basis = str(transfer.get("basis") or "")
    if basis == "cash":
        subtype = "Cash"
    elif basis == "in_specie":
        subtype = "In Specie"
    else:
        raise ValueError(
            "Say whether this is a cash or in-specie transfer before it is placed at Seccl."
        )
    address: dict[str, str] = {
        "address1": _stated(transfer, "provider_address_line1"),
        "postCode": _stated(transfer, "provider_postcode"),
        "country": _stated(transfer, "provider_country"),
    }
    if line2 := _stated(transfer, "provider_address_line2"):
        address["address2"] = line2
    if city := _stated(transfer, "provider_city"):
        address["address3"] = city
    transaction: dict[str, Any] = {
        "transactionType": "Product Transfer",
        "transactionSubType": subtype,
        "movementType": "In",
        "previousProvider": transfer["masterparty_id"],
        "previousProviderRef": transfer.get("policy_number") or "",
        "transferAccountAddress": address,
        "transferValue": transfer["total_value"],
        "externalReference": transfer["uid"],
    }
    if str(transfer.get("transfer_type") or "") == "pension":
        crystallisation = str(transfer.get("crystallisation") or "").strip().lower()
        transaction["productDrawdownStatus"] = DRAWDOWN_FOR[crystallisation]
        # Safeguarded rights, not "this is a pension". See the module docstring.
        transaction["isPensionTransfer"] = bool(transfer.get("safeguarded_benefits"))
        if transfer.get("safeguarded_benefits"):
            transaction["fcaReference"] = _stated(transfer, "fca_reference")
    return transaction


class SecclPlacedWithoutId(Exception):
    """The product transfer was created. Posting it again would duplicate it."""


def _client() -> Any:
    if not configured():
        from app.integrations.seccl.connection import why_not_configured

        raise SecclNotConfigured(why_not_configured() or "Seccl is not configured.")
    from seccl.client import SecclClient

    return SecclClient(seccl_settings())


def transaction_id_of(created: dict[str, Any], members: list[dict[str, Any]]) -> str | None:
    """The id cancel and refresh address.

    The group answers with a ``linkId``. The product-transfer transaction inside the group is
    what ``requestCancellation`` takes, so that id wins when the follow-up read found it.
    """
    for member in members:
        if str(member.get("transactionType") or "") == "Product Transfer" and member.get("id"):
            return str(member["id"])
    for key in ("id", "linkId"):
        if created.get(key):
            return str(created[key])
    data = created.get("data")
    if isinstance(data, dict):
        for key in ("id", "linkId"):
            if data.get(key):
                return str(data[key])
    return None


def link_id_of(created: dict[str, Any]) -> str | None:
    for source in (created, created.get("data") if isinstance(created.get("data"), dict) else {}):
        if isinstance(source, dict) and source.get("linkId"):
            return str(source["linkId"])
    return None


def transfer_status_of(record: dict[str, Any]) -> str | None:
    status = record.get("transferStatus") or record.get("status")
    if status is None:
        return None
    text = str(status).strip()
    return text or None


async def place_transfer(transfer: dict[str, Any]) -> tuple[str, str | None]:
    """Create the product transfer. Returns its id, and Seccl's status when one came back."""
    transaction = build_product_transfer(transfer)
    account_id = str(transfer["seccl_account_id"])
    created: dict[str, Any] = {}
    members: list[dict[str, Any]] = []
    try:
        async with _client() as api:
            created = await api.create_transaction_group(account_id, [transaction])
            if not isinstance(created, dict):
                created = {}
            link_id = link_id_of(created)
            if link_id:
                # A failed follow-up read must not throw away the link id the create just returned.
                try:
                    members = await api.transaction_group(link_id)
                except Exception:
                    members = []
    except SecclNotConfigured:
        raise
    except Exception as exc:
        from seccl.client import SecclApiError

        if isinstance(exc, SecclApiError):
            raise SecclUnavailable(
                "Seccl could not accept this transfer. Try again later."
            ) from exc
        raise
    identifier = transaction_id_of(created, members)
    if not identifier:
        raise SecclPlacedWithoutId(
            "Seccl accepted the transfer but did not return an id, so it cannot be refreshed. "
            "It has been held so it is not placed twice."
        )
    observed = None
    for member in members:
        if str(member.get("id") or "") == identifier:
            observed = transfer_status_of(member)
            break
    return identifier, observed


async def fetch_transfer(transfer: dict[str, Any]) -> dict[str, Any]:
    """The product transfer Seccl currently has, or empty when we have no id to ask with."""
    identifier = transfer.get("seccl_transfer_id")
    if not identifier:
        raise ValueError("This transfer has not been placed at Seccl.")
    try:
        async with _client() as api:
            record = await api.transaction(str(identifier))
    except SecclNotConfigured:
        raise
    except Exception as exc:
        from seccl.client import SecclApiError

        if isinstance(exc, SecclApiError):
            raise SecclUnavailable("Seccl could not be reached. Try again later.") from exc
        raise
    return record if isinstance(record, dict) else {}


async def cancel_transfer(transfer: dict[str, Any], *, current_status: str | None) -> None:
    """Ask Seccl to cancel. A 200 is a request, not proof the transfer has stopped."""
    from app.transfers import seccl_cancellable

    if not seccl_cancellable(current_status):
        raise TransferNotCancellable(
            "Seccl will only cancel a transfer that is still with the previous provider "
            "or its documentation checks. Refresh the transfer and try again if that has changed."
        )
    identifier = transfer.get("seccl_transfer_id")
    if not identifier:
        raise ValueError("This transfer has not been placed at Seccl.")
    try:
        async with _client() as api:
            await api.request_transfer_cancellation(str(identifier))
    except SecclNotConfigured:
        raise
    except Exception as exc:
        from seccl.client import SecclApiError

        if isinstance(exc, SecclApiError):
            raise SecclUnavailable(
                "Seccl could not accept the cancellation. Try again later."
            ) from exc
        raise
