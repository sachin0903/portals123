"""Record a Graphene transfer against a Morgan Lloyd scheme, and read its progress back.

``platform.transfers``' columns were written to track ``cam_createtransfer``'s required list
(0051 says so in as many words) and **nothing had ever sent one**. So this is the adapter that
makes that claim true, plus the read that drives the mirror `0056` added.

FOUR THINGS ABOUT THE VENDOR CONTRACT THAT COST MORE THAN THEY LOOK.

**``cam_uncrystalllisedtransfervalue`` has three Ls.** Verbatim, do not correct it. The column is
spelled properly and the mangling happens here, because a wire-format typo is not a domain
concept — the note ``app.transfers`` already carries about itself.

**``cam_status`` is not ``transferstatus``.** The record's own status (``AssetStatus``, fixed at
Transferring=4 on create) and the six-value progress set (:class:`morgan_lloyd.enums.
TransferStatus`, what a read returns and what ``platform.transfers.ml_status`` mirrors) are
different option sets that both look like "the status". Confusing them is how a transfer would
be mirrored as "Awaiting forms" because the vendor said 4 about something else.

**THREE REQUIRED FIELDS**, two of which are columns as of 0147:

* ``cam_typeoftransfer`` — Cash or In Specie. Cash means the ceding scheme LIQUIDATES the
  portfolio. Read from ``platform.transfers.basis``, falling back to ``data.basis`` for rows
  raised before the column existed, then to ``transfer_type`` only where that CHECK holds
  ``cash``/``in_specie`` (it mixes wrappers and bases).
* ``cam_transfertype`` — Full or Partial. Read from ``platform.transfers.extent``, then
  ``data.extent``.
* ``cam_pensiontype`` — required only for a pension policy (the vendor's ``*``), and its option
  set is 20 members of the ceding scheme's product type. A column as of 0173; ``data`` remains
  the fallback for rows raised before the drawer asked.

None is guessed. A missing material one is a refusal NAMING it rather than a default — sending
Cash for an in-specie transfer instructs somebody to sell a client's portfolio, and the vendor
cannot object because both values are valid. ``pension_type`` is sent when the row has it
(the column, then ``data``) and omitted otherwise — the vendor's ``*`` — rather than guessed.

A successful create returns ``transferId`` — the ``cam_personalpolicyid`` GUID — which goes on
``platform.transfers.ml_transfer_id``. That is a COLUMN rather than a `platform.links` row
because a transfer is one-to-one with its vendor record and 0051 already declared the column;
`links` is the polymorphic bridge for an entity that may be reached at several providers at once.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import httpx
from morgan_lloyd import MorganLloydError
from morgan_lloyd.enums import (
    AssetStatus,
    AssetType,
    CrystallisedType,
    TransferType,
    TypeOfTransfer,
)

from app.integrations.morgan_lloyd.benefits import (
    MorganLloydUnavailable,
    _explain,
    _settings,
    configured,
    unconfigured,
)

# THE OPTION-SET INTEGERS ARE IMPORTED, NOT COPIED. `morgan_lloyd.enums` is the vendored,
# manifest-pinned vocabulary and is what gets rebuilt when the vendor renumbers; the literals that
# used to sit here named the very enum they should have imported, in a comment beside each one, and
# Dynamics accepts ANY valid option-set integer — so a stale copy is a 200 that files the transfer
# under the wrong basis. `cam_typeoftransfer` is the field whose wrong value instructs the ceding
# scheme to LIQUIDATE the client's portfolio, and this module's own docstring says the vendor
# cannot object because both values are well-formed.

# The ceding policy's asset type. Only Pension Plan is documented, and it is the only one a
# pension transfer uses. (The vendor labels it "Pension Plan" on Create Transfer and
# "Pension Plans" in the option set — one member, two spellings, so the enum is the address.)
ASSET_TYPE_PENSION_PLAN = int(AssetType.PENSION_PLANS)

# The RECORD's status, fixed on create. Not the progress set (`TransferStatus`).
RECORD_STATUS_TRANSFERRING = int(AssetStatus.TRANSFERRING)

#: HOW the money moves. Cash means the ceding scheme sells.
BASIS_TO_ML: dict[str, int] = {
    "cash": int(TypeOfTransfer.CASH),
    "in_specie": int(TypeOfTransfer.IN_SPECIE),
}

#: How MUCH of the ceding pot moves.
EXTENT_TO_ML: dict[str, int] = {
    "full": int(TransferType.FULL),
    "partial": int(TransferType.PARTIAL),
}

#: `platform.transfers.transfer_type` values that name a BASIS rather than a wrapper. The CHECK
#: mixes the two (0051), so the column answers `cam_typeoftransfer` only for these.
BASIS_TRANSFER_TYPES = frozenset(BASIS_TO_ML)

#: Wrappers that make `cam_pensiontype` conditionally required. Mirrors `sync.PENSION_PRODUCTS`
#: on the wrapper axis rather than the product one.
PENSION_TRANSFER_TYPES = frozenset({"pension"})

_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


class TransferNotCancellable(RuntimeError):
    """The Morgan Lloyd transfer has moved too far to be withdrawn.

    Checked against the vendor's own progress rather than our mirror, for the reason
    `contributions.NotAnApplication` gives: the mirror is only as fresh as the last refresh, and
    the whole point of the gate is that somebody else may have moved the record on.
    """


def _money(value: Any) -> float:
    """`decimal` on the vendor's side, and a `Decimal` is not JSON-serialisable."""
    return float(Decimal(str(value)))


def _stated(transfer: dict[str, Any], key: str) -> str:
    """A value out of `platform.transfers.data`, VERBATIM apart from surrounding whitespace.

    `data` is the row's own jsonb (0051) and is where the vendor facts we hold no column for live
    — the ceding address. `basis`/`extent` earned columns in 0147; `pension_type` in 0173. This
    still reads them from jsonb as a fallback for rows raised before that.
    """
    data = transfer.get("data")
    if isinstance(data, dict):
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _setting(transfer: dict[str, Any], key: str) -> str:
    """The same, normalised for a lookup against one of our own closed vocabularies.

    SEPARATE FROM :func:`_stated` on purpose. This one is for `basis`/`extent`, which are keys
    into `BASIS_TO_ML`/`EXTENT_TO_ML`: the value may have been typed by a person or copied from
    the vendor's own label, so "In Specie", "in-specie" and `in_specie` have to be one answer —
    the alternative is a refusal that reads as "you did not say" to somebody who did.

    It is emphatically NOT for the ceding provider's ADDRESS. Folding "Aviva, Wellington Row" to
    lower case fails nothing; it just puts a mangled address on a real transfer instruction, which
    is the class of defect nothing reports. Normalising only inside a lookup against a set we own
    is what keeps that distinction structural.
    """
    return re.sub(r"[\s-]+", "_", _stated(transfer, key).lower())


def _column_or_data(transfer: dict[str, Any], key: str) -> str:
    """A folded lookup value: the column first, then `data`, then nothing.

    The column is 0147's home for `basis`/`extent`. `data` is the fallback for a row raised
    before the drawer wrote either. An empty column must not shadow a filled jsonb key.
    """
    raw = transfer.get(key)
    if raw is not None and str(raw).strip():
        return re.sub(r"[\s-]+", "_", str(raw).strip().lower())
    return _setting(transfer, key)


def basis_of(transfer: dict[str, Any]) -> str:
    """Cash or in specie, from the column, then `data.basis`, then `transfer_type` where it says so.

    The column first: a row that states its basis on `platform.transfers.basis` means it.
    `transfer_type` carrying `pension` says nothing about the basis at all.
    """
    explicit = _column_or_data(transfer, "basis")
    if explicit:
        return explicit
    declared = str(transfer.get("transfer_type") or "").strip().lower()
    return declared if declared in BASIS_TRANSFER_TYPES else ""


def extent_of(transfer: dict[str, Any]) -> str:
    """Full or partial, from the column, then `data.extent`."""
    return _column_or_data(transfer, "extent")


def missing_fields(transfer: dict[str, Any]) -> list[str]:
    """Human-readable names of what Morgan Lloyd needs and this transfer hasn't got.

    Returned rather than substituted, the posture the contact and contribution mappers take: the
    target is a real instruction to move a client's pension, and a default for `basis` in
    particular would tell a ceding scheme to liquidate a portfolio.
    """
    absent: list[str] = []
    if not transfer.get("ml_scheme_ref"):
        absent.append("a Morgan Lloyd scheme reference for the receiving account")
    if not transfer.get("ceding_provider"):
        absent.append("the ceding provider's name")
    if not transfer.get("policy_number"):
        absent.append("the ceding policy number")

    # All three values are required by the vendor and each is a separate number to them. A NULL is
    # not zero: "we do not know the crystallised split" and "none of it is crystallised" are
    # different facts, and sending 0 for the first asserts the second.
    for column, label in (
        ("total_value", "the total transfer value"),
        ("crystallised_value", "the crystallised value (enter 0 if none is crystallised)"),
        ("uncrystallised_value", "the uncrystallised value (enter 0 if none is uncrystallised)"),
    ):
        if transfer.get(column) is None:
            absent.append(label)

    basis = basis_of(transfer)
    if basis not in BASIS_TO_ML:
        absent.append(
            "whether this is a cash or in-specie transfer — Morgan Lloyd requires it and cash "
            "instructs the ceding scheme to sell"
        )
    if extent_of(transfer) not in EXTENT_TO_ML:
        absent.append("whether this is a full or partial transfer")
    if str(transfer.get("transfer_type") or "").strip().lower() in PENSION_TRANSFER_TYPES:
        pension_type = transfer.get("pension_type")
        stated = (
            str(pension_type).strip()
            if pension_type is not None and str(pension_type).strip()
            else _stated(transfer, "pension_type")
        )
        if not stated:
            absent.append("the ceding scheme's pension type")
    return absent


def build_transfer(transfer: dict[str, Any]) -> dict[str, Any]:
    """Map a `platform.transfers` row onto a `cam_createtransfer` payload."""
    absent = missing_fields(transfer)
    if absent:
        raise ValueError(
            "Morgan Lloyd requires these details before a transfer can be recorded: "
            + ", ".join(absent)
        )

    payload: dict[str, Any] = {
        "schemeReference": transfer["ml_scheme_ref"],
        "cam_policyno": transfer["policy_number"],
        "cam_providername": transfer["ceding_provider"],
        "cam_assettype": ASSET_TYPE_PENSION_PLAN,
        # The RECORD's status, not the progress set. A transfer we are creating is by definition
        # transferring; the progress comes back from a read.
        "cam_status": RECORD_STATUS_TRANSFERRING,
        # Three Ls, verbatim.
        "cam_uncrystalllisedtransfervalue": _money(transfer["uncrystallised_value"]),
        "cam_crystallisedtransfervalue": _money(transfer["crystallised_value"]),
        # `cam_nonprtv` is the WHOLE transfer value despite the name reading as the
        # non-protected slice — 0051 records the same trap against `total_value`.
        "cam_nonprtv": _money(transfer["total_value"]),
        "cam_typeoftransfer": BASIS_TO_ML[basis_of(transfer)],
        "cam_transfertype": EXTENT_TO_ML[extent_of(transfer)],
    }

    # `cam_pensiontype` is `Yes*` — required only for a pension policy. Sent when the row says
    # so (the column, then `data`) and OMITTED otherwise: guessing a ceding product type is a
    # wrong instruction that Dynamics will accept.
    pension_type = transfer.get("pension_type")
    pension_type_text = (
        str(pension_type).strip()
        if pension_type is not None and str(pension_type).strip()
        else _stated(transfer, "pension_type")
    )
    if pension_type_text:
        try:
            payload["cam_pensiontype"] = int(pension_type_text)
        except ValueError as exc:
            raise ValueError(
                f"pension_type is {pension_type_text!r}, which is not one of Morgan Lloyd's "
                "PensionType option-set numbers (morgan_lloyd.enums.PensionType)."
            ) from exc

    crystallisation = (
        str(transfer.get("crystallisation") or _stated(transfer, "crystallisation")).strip().lower()
    )
    if crystallisation == "partial":
        payload["cam_crystallised"] = int(CrystallisedType.PARTIAL)
    elif crystallisation in {"crystallised", "uncrystallised", "full"}:
        payload["cam_crystallised"] = int(CrystallisedType.FULL)
    else:
        cry = transfer.get("crystallised_value")
        uncry = transfer.get("uncrystallised_value")
        both_positive = (
            cry is not None
            and uncry is not None
            and Decimal(str(cry)) > 0
            and Decimal(str(uncry)) > 0
        )
        if both_positive:
            payload["cam_crystallised"] = int(CrystallisedType.PARTIAL)
        elif cry is not None or uncry is not None:
            payload["cam_crystallised"] = int(CrystallisedType.FULL)

    # The ceding provider's address, sent only where we hold it — an empty string is a value to
    # Dynamics and would overwrite.
    for key, field in (
        ("provider_address_line1", "cam_provideraddressline1"),
        ("provider_address_line2", "cam_provideraddressline2"),
        ("provider_address_line3", "cam_provideraddressline3"),
        ("provider_city", "cam_providercity"),
        ("provider_county", "cam_providercounty"),
        ("provider_postcode", "cam_providerpostcode"),
    ):
        if value := _stated(transfer, key):  # verbatim — an address is not a lookup key
            payload[field] = value

    # `transferNotes` creates a Note against the Transfer — the adviser's own words, which is
    # what a chasing conversation at the scheme administrator actually runs on.
    if note := transfer.get("note"):
        payload["transferNotes"] = str(note)
    return payload


def _require_client() -> Any:
    """The vendor client, or the 503 that says why it is absent."""
    if not configured():
        raise unconfigured("MORGAN_LLOYD_TENANT_ID / _CLIENT_ID / _CLIENT_SECRET")
    from morgan_lloyd.client import MorganLloydClient

    return MorganLloydClient


def _created_id(response: dict[str, Any]) -> str | None:
    """The `cam_personalpolicyid` GUID out of the action envelope.

    The same envelope trap `contacts._created_id` documents, and the same reason to match by
    SHAPE rather than by a key name the docs do not pin down: `cam_createcontact` created the
    person every time and we discarded the id, so each retry duplicated them. A transfer is
    cheaper to duplicate than a human being and still not free — it is an instruction to a
    ceding scheme.
    """
    nested = response.get("transferId")
    if isinstance(nested, dict):
        for value in nested.values():
            if isinstance(value, str) and (found := _GUID.search(value)):
                return found.group(0)
    if isinstance(nested, str) and (found := _GUID.search(nested)):
        return found.group(0)
    for key in ("cam_personalpolicyid", "id"):
        value = response.get(key)
        if isinstance(value, str) and _GUID.fullmatch(value.strip("{}")):
            return value.strip("{}")
    inner = response.get("response")
    if isinstance(inner, dict):
        for value in inner.values():
            if isinstance(value, str) and (found := _GUID.search(value)):
                return found.group(0)
    if isinstance(inner, str) and (found := _GUID.search(inner)):
        return found.group(0)
    if isinstance(message := response.get("message"), str) and (found := _GUID.search(message)):
        return found.group(0)
    return None


async def create_transfer(transfer: dict[str, Any]) -> str:
    """Create the `cam_personalpolicy` record and return its GUID.

    Raises ``ValueError`` when required details are missing (caller turns that into a 422),
    ``MorganLloydNotConfigured`` without credentials (503), ``MorganLloydUnavailable`` when
    Dynamics or Azure refuses (502).
    """
    payload = build_transfer(transfer)  # raises for anything missing
    client = _require_client()
    try:
        async with client(settings=_settings()) as api:
            response = await api.create_transfer(payload)
    # THE CLIENT DOES NOT RAISE httpx. `morgan_lloyd._base` wraps every transport and status
    # error into `MorganLloydApiError`, which descends from `MorganLloydError` and NOT from
    # `httpx.HTTPError` — so an `except httpx.HTTPError` here caught nothing and a rejected vendor
    # call, or an Azure Conditional Access block, escaped as an unhandled 500 with a stack trace
    # instead of the 502 this route's docstring promises. httpx stays in the tuple for anything
    # raised outside the client.
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc

    guid = _created_id(response)
    if guid is None:
        raise MorganLloydUnavailable(
            f"Morgan Lloyd recorded the transfer but returned no id (keys: {sorted(response)}). "
            "It has not been linked, so a retry would create a second instruction — check the "
            "scheme's transfers before retrying."
        )
    return guid


async def fetch_status(transfer: dict[str, Any]) -> int | None:
    """Read Morgan Lloyd's transfer progress back, or None if they no longer list it.

    `cam_getpensiontransfer` is a READ implemented as a POST, and it is keyed by the CLIENT's
    Dynamics GUID plus the ceding policy number — not by the transfer's own id, which the vendor
    gives us and then offers no lookup for. So a refresh needs the client link as well as the
    transfer, and says so rather than answering "not found" for a transfer that exists.

    Returns the `transferstatus` progress value (0-5), which is what
    `app.transfers.record_ml_status` mirrors — NOT the record's `cam_status`.
    """
    contact_guid = transfer.get("ml_contact_id")
    policy = transfer.get("policy_number")
    if not contact_guid:
        raise ValueError(
            "This client has not been pushed to Morgan Lloyd, so their transfers cannot be read "
            "back — Morgan Lloyd looks a transfer up by the client's Dynamics id."
        )
    if not policy:
        raise ValueError("This transfer has no ceding policy number, so it cannot be looked up.")

    client = _require_client()
    try:
        async with client(settings=_settings()) as api:
            answer = await api.get_transfer(str(contact_guid), str(policy))
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc

    status = answer.get("transferstatus")
    if status is None and isinstance(inner := answer.get("response"), dict):
        status = inner.get("transferstatus")
    return int(status) if status is not None else None


async def cancel_transfer(transfer: dict[str, Any]) -> dict[str, Any]:
    """Withdraw a transfer at Morgan Lloyd (`transferstatus` -> No longer transferring).

    THE VENDOR DOCUMENTS NO CANCEL ACTION. What exists is the entity PATCH
    (`cam_personalpolicies(<guid>)`), whose documented body is the asset-transfer fields, and the
    progress value 5 "No longer transferring" — so this sets the progress on the record.

    The gate is read first, not taken from our mirror: past "funds received" the ceding scheme
    has already paid, and withdrawing then is not a cancellation but a return of money that has
    to be arranged with two counterparties. Refusing with the vendor's own current status named is
    the only answer that tells an adviser what actually has to happen instead.
    """
    guid = transfer.get("ml_transfer_id")
    if not guid:
        raise ValueError("This transfer has not been recorded in Morgan Lloyd.")

    from app.transfers import (
        ML_FUNDS_RECEIVED,
        ML_NO_LONGER_TRANSFERRING,
        ML_STATUS_LABELS,
    )

    current = await fetch_status(transfer)
    if current == ML_NO_LONGER_TRANSFERRING:
        return {"ml_status": ML_NO_LONGER_TRANSFERRING, "response": {"already_cancelled": True}}
    if current is not None and current >= ML_FUNDS_RECEIVED:
        raise TransferNotCancellable(
            f"Morgan Lloyd has this transfer at '{ML_STATUS_LABELS.get(current, current)}' — the "
            "money has already moved, so it cannot be withdrawn. Arrange the return with the "
            "ceding scheme and Morgan Lloyd instead."
        )

    client = _require_client()
    try:
        async with client(settings=_settings()) as api:
            response = await api.update_transfer(
                str(guid), {"cam_transferstatus": ML_NO_LONGER_TRANSFERRING}
            )
    except (MorganLloydError, httpx.HTTPError) as exc:
        raise MorganLloydUnavailable(_explain(exc)) from exc
    return {"ml_status": ML_NO_LONGER_TRANSFERRING, "response": response}
