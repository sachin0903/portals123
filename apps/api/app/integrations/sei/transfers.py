"""SEI's half of a transfer: a cash receipt, a transfer-in, a free receipt, or a status poll.

SEI has no transfer entity in Morgan Lloyd's sense. Cash is an anticipated receipt — the same
call a contribution uses, with the firm's *transfer* description and treatment codes. An
in-specie transfer that SEI itself is chasing is ``transferIn``. When Morgan Lloyd already
asked the ceding scheme, the SEI leg is a free receipt of assets that are arriving, not a
second instruction to the ceding provider.

A missing document reference is a 422 that names it. A transfer-in does not send asset
lines: SEI accepts the request without them. This module does not upload forms.
"""

from __future__ import annotations

from typing import Any

from app.integrations.sei.cash_receipts import (
    CONFIG_PLATFORM_USER,
    SeiNotConfigured,
    SeiUnavailable,
    _settings,
    configured,
    resolve_transfer_codes,
)


def _list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _assets(transfer: dict[str, Any]) -> list[dict[str, Any]]:
    data = transfer.get("data")
    if not isinstance(data, dict):
        return []
    return _list(data.get("assets"))


#: `crm.contacts.national_identifier_type` values that are a UK National Insurance number.
#: SEI's `externalPrimaryTaxInfo` is the primary client's tax number at the ceding scheme
#: ("SSN # or Tax #"). For a UK resident that number is the NINO.
_NINO_TYPES = frozenset({"nino", "national_insurance_number"})


def _documents(transfer: dict[str, Any]) -> list[dict[str, Any]]:
    data = transfer.get("data")
    if not isinstance(data, dict):
        return []
    return _list(data.get("documents"))


def signing_tax_id(transfer: dict[str, Any]) -> str:
    """The tax number the ceding scheme holds for the person signing the transfer.

    An explicit `data.signing_authority_tax_id` wins, because that is the copy the ceding
    scheme was given. Otherwise a UK client's National Insurance number is that number:
    `crm.contacts.national_identifier` where the type is a NINO.
    """
    raw = transfer.get("data")
    data: dict[str, Any] = raw if isinstance(raw, dict) else {}
    stated = str(data.get("signing_authority_tax_id") or "").strip()
    if stated:
        return stated
    kind = str(transfer.get("national_identifier_type") or "").strip().lower()
    number = str(transfer.get("national_identifier") or "").strip()
    if number and kind in _NINO_TYPES:
        return number
    return ""


def _asset_ready(asset: dict[str, Any]) -> bool:
    return bool(
        asset.get("identifier")
        and asset.get("identifier_type")
        and asset.get("issue_country")
        and asset.get("units")
    )


def missing_transfer_in(transfer: dict[str, Any], codes: dict[str, Any]) -> list[str]:
    """What ``transferIn`` needs. Each line is the thing an operator can go and set."""
    absent: list[str] = []
    if not transfer.get("sei_account_number"):
        absent.append("the account's SEI account number (links, provider 'sei')")
    if not codes.get("platform_user"):
        absent.append(f"the tenant's SEI platform user (provider config '{CONFIG_PLATFORM_USER}')")
    if not transfer.get("policy_number"):
        absent.append("the ceding scheme's account number (policy number)")
    if not transfer.get("ceding_provider"):
        absent.append("the ceding provider's name")
    raw = transfer.get("data")
    data: dict[str, Any] = raw if isinstance(raw, dict) else {}
    if not data.get("external_account_type"):
        absent.append("the ceding account type (data.external_account_type)")
    if not data.get("signing_authority_name") and not transfer.get("client_name"):
        absent.append("the primary signing authority's name")
    if not signing_tax_id(transfer):
        absent.append("the client's National Insurance number")
    documents = _documents(transfer)
    if not documents or not all(doc.get("document_reference_id") for doc in documents):
        absent.append(
            "uploaded transfer form references (data.documents[].document_reference_id) — "
            "SEI refuses a transfer-in that has none, and this action does not upload them"
        )
    return absent


def build_transfer_in(transfer: dict[str, Any], codes: dict[str, Any]) -> dict[str, Any]:
    absent = missing_transfer_in(transfer, codes)
    if absent:
        raise ValueError(
            "SEI requires these details before a transfer in can be submitted: " + ", ".join(absent)
        )
    data = transfer["data"] if isinstance(transfer.get("data"), dict) else {}
    basis = transfer.get("basis")
    extent = transfer.get("extent") or "full"
    # 1 encash (cash), 2 in specie. 1 full, 2 partial.
    transfer_type_id = 1 if basis == "cash" else 2
    instruction_id = 1 if extent == "full" else 2
    signing_name = data.get("signing_authority_name") or transfer.get("client_name")
    body: dict[str, Any] = {
        "platformUser": codes["platform_user"],
        "account": {"accountNumber": transfer["sei_account_number"]},
        "externalAccount": {
            "externalAccountName": transfer["ceding_provider"],
            "externalAccountNumber": transfer["policy_number"],
            "externalAccountType": data.get("external_account_type"),
        },
        "primarySigningAuthority": {
            "primarySigningAuthorityName": signing_name,
            "externalPrimaryTaxInfo": signing_tax_id(transfer),
        },
        "transferType": {"transferTypeId": transfer_type_id},
        "assetTransferInstruction": {"assetTransferInstructionId": instruction_id},
        "documents": [
            {"documentReferenceId": doc["document_reference_id"]} for doc in _documents(transfer)
        ],
    }
    return body


def missing_free_receipt(transfer: dict[str, Any], codes: dict[str, Any]) -> list[str]:
    absent: list[str] = []
    if not transfer.get("sei_account_number"):
        absent.append("the account's SEI account number (links, provider 'sei')")
    if not transfer.get("sei_portfolio_id"):
        absent.append("the account's SEI portfolio id (links, link type 'portfolio')")
    if not codes.get("firm_id"):
        absent.append("the tenant's SEI firm id (provider config 'firm_id')")
    if not codes.get("platform_user"):
        absent.append(f"the tenant's SEI platform user (provider config '{CONFIG_PLATFORM_USER}')")
    assets = _assets(transfer)
    if not assets or not all(_asset_ready(asset) for asset in assets):
        absent.append(
            "each asset's identifier, identifier type, issue country and units (data.assets)"
        )
    return absent


def build_free_receipt(transfer: dict[str, Any], codes: dict[str, Any]) -> dict[str, Any]:
    """The back-office receipt. ``platforUser`` is SEI's spelling, not a typo to correct."""
    absent = missing_free_receipt(transfer, codes)
    if absent:
        raise ValueError(
            "SEI requires these details before the assets can be registered: " + ", ".join(absent)
        )
    return {
        "platforUser": codes["platform_user"],
        "portfolio": {
            "accountNumber": transfer["sei_account_number"],
            "portfolioId": transfer["sei_portfolio_id"],
            "firmId": codes["firm_id"],
        },
        "receiptAsset": [
            {
                "asset": {
                    "externalIdentifierType": {"identifier": asset["identifier_type"]},
                    "externalIdentifierValue": asset["identifier"],
                    "issueCountry": asset["issue_country"],
                },
                "units": asset["units"],
            }
            for asset in _assets(transfer)
        ],
    }


def activity_id_of(body: dict[str, Any]) -> str | None:
    for key in ("transferActivityId", "activityId", "activity_id", "eventId", "event_id"):
        if body.get(key):
            return str(body[key])
    return None


def _client() -> Any:
    settings = _settings()
    if not configured() or not settings.configured:
        raise SeiNotConfigured("SEI is unavailable for this firm. Contact support.")
    from sei import SeiClient

    return SeiClient(settings), settings


async def _call(method: str, *args: Any) -> Any:
    from sei import SeiApiError, SeiAuthError, SeiConfigError

    client, _settings_obj = _client()
    try:
        async with client as api:
            return await getattr(api, method)(*args)
    except SeiConfigError as exc:
        raise SeiNotConfigured("SEI is unavailable for this firm. Contact support.") from exc
    except (SeiAuthError, SeiApiError) as exc:
        raise SeiUnavailable("SEI could not accept this request. Try again later.") from exc


async def submit_transfer_in(transfer: dict[str, Any]) -> dict[str, Any]:
    codes = resolve_transfer_codes(transfer.get("sei_config"))
    attributes = build_transfer_in(transfer, codes)
    result = await _call("transfer_in", attributes)
    body = result if isinstance(result, dict) else {}
    return {"activity_id": activity_id_of(body), "body": body}


async def register_free_receipt(transfer: dict[str, Any]) -> dict[str, Any]:
    codes = resolve_transfer_codes(transfer.get("sei_config"))
    attributes = build_free_receipt(transfer, codes)
    result = await _call("free_receipt", attributes)
    body = result if isinstance(result, dict) else {}
    return {"activity_id": activity_id_of(body), "body": body}


def _status_code(record: dict[str, Any]) -> str | None:
    code = record.get("transferStatusCode")
    if code is None:
        return None
    return str(code).strip() or None


def require_transfer_in(transfer: dict[str, Any]) -> None:
    """Pending-transfer status only lists ``transferIn``.

    A currency receipt and a free receipt also store an activity id. That id is not in
    the pending-transfer list, so polling it 422s after a lodge that succeeded.
    """
    if transfer.get("route") == "sei" and transfer.get("basis") == "in_specie":
        return
    raise ValueError(
        "SEI's pending-transfer status only tracks a transfer in. "
        "A cash receipt or a free receipt is followed on the activity, not this call."
    )


async def poll_pending(transfer: dict[str, Any]) -> dict[str, Any]:
    """One pending-transfer row for this transfer-in.

    More than one row and no stored activity id is a refusal: attaching the only pending
    instruction on the account would advance the wrong transfer.
    """
    require_transfer_in(transfer)
    codes = resolve_transfer_codes(transfer.get("sei_config"))
    absent: list[str] = []
    if not transfer.get("sei_account_number"):
        absent.append("the account's SEI account number (links, provider 'sei')")
    if not codes.get("firm_id"):
        absent.append("the tenant's SEI firm id (provider config 'firm_id')")
    if not codes.get("platform_user"):
        absent.append(f"the tenant's SEI platform user (provider config '{CONFIG_PLATFORM_USER}')")
    if absent:
        raise ValueError(
            "SEI requires these details before a transfer can be refreshed: " + ", ".join(absent)
        )
    wanted = str(transfer.get("sei_activity_id") or "")
    if not wanted:
        raise ValueError(
            "This transfer has no SEI activity id, so a pending row on the account "
            "was not attached to it."
        )
    firm = codes["firm_id"]
    request = {
        "data": {
            "type": "PendingTransferStatusRequest",
            "attributes": {
                "firmId": int(firm) if str(firm).isdigit() else firm,
                "platformUser": codes["platform_user"],
                "accountNumber": transfer["sei_account_number"],
                "transferActivityId": wanted,
                "includeCancelledAndDeleted": True,
                "latestTransferNotes": True,
            },
        }
    }
    rows = await _call("pending_transfers", request)
    records = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    records = [row for row in records if str(row.get("transferActivityId") or "") == wanted]
    if not records:
        raise ValueError("SEI does not list a pending transfer for this account.")
    record = records[0]
    return {
        "activity_id": str(record.get("transferActivityId") or wanted or "") or None,
        "status_code": _status_code(record),
        "status_label": record.get("transferStatus"),
    }
