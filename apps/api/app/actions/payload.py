"""Shared bits of an action step: who it is for, and how a vendor answer becomes an attempt."""

from __future__ import annotations

from typing import Any

from app.actions.attempt import Attempt, OperationError


def _sent(updated: dict[str, Any], *, method: str, url: str) -> Attempt:
    return Attempt(
        ok=True,
        method=method,
        url=url,
        request=updated.get("request"),
        response=updated,
    )


def transfer_attempt(result: dict[str, Any], *, method: str, url: str) -> Attempt:
    """The transfer the workbench shows. An accepted hold is done; a refusal before that is not.

    ``provider_error`` on a row that is still ``requested`` is the provider turning the
    instruction away, and the workbench keeps that row so it can be sent again. A hold
    after the provider accepted (no id to address a second call) is finished: sending
    again would open another instruction.
    """
    if result.get("status") == "skipped":
        return Attempt(ok=True, skipped=True, method=method, url=url, response=result)
    accepted = bool(
        result.get("seccl_transfer_id")
        or result.get("seccl_placed")
        or result.get("sei_activity_id")
        or result.get("sei_accepted")
        or result.get("ml_transfer_id")
    )
    status = str(result.get("status") or "")
    error = result.get("provider_error")
    refused = bool(error) and not accepted and status in {"", "requested"}
    return Attempt(
        ok=not refused,
        method=method,
        url=url,
        response=result,
        error=str(error) if refused else None,
    )


def _from_report(result: dict[str, Any], *, method: str, url: str) -> Attempt:
    """The attempt is the vendor call. The book report around it is not.

    A submitted group carries ``request`` (the body we posted) and ``response`` (the record
    the vendor returned). A skip or a miss before the call has no vendor body, so the report
    itself is what the run shows.
    """
    error = result.get("provider_error") or result.get("detail")
    status = str(result.get("status") or "")
    failed = bool(result.get("provider_error") or status in {"failed", "blocked", "unavailable"})
    vendor = result.get("response")
    recorded = vendor if isinstance(vendor, dict) else result
    return Attempt(
        ok=not failed,
        method=method,
        url=url,
        request=result.get("request"),
        response=recorded,
        error=str(error or status) if failed else None,
    )


async def _ids(payload: dict[str, Any]) -> tuple[int, int | None, int | None]:
    tenant_id = payload.get("tenant_id")
    if tenant_id is None:
        raise OperationError("This action needs the firm.", permanent=True)
    account_id = payload.get("account_id")
    contact_id = payload.get("contact_id")
    return (
        int(tenant_id),
        int(account_id) if account_id is not None else None,
        int(contact_id) if contact_id is not None else None,
    )
