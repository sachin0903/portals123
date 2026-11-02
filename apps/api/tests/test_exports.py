"""`POST /exports/record` — the fire-and-record endpoint behind every listing export.

The route does no work of its own (see app/routers/exports.py's module docstring): CSV, Excel and
PDF are all built on the client now, and this call exists only to BE a mutating request
`ActionEventsMiddleware` can turn into an `audit.actions` row. So what matters here is the same
thing that mattered for the old render-worker endpoint — the gate — plus the one thing that
replaced it: the format and listing land in the event's subject.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException

from app import audit_actions
from app.auth import Principal
from app.routers import exports


def _principal(*perms: str) -> Principal:
    return Principal(
        user_id="x",
        organisation_id="x",
        role="adviser",
        permissions=list(perms) if perms else ["listings:export"],
    )


async def test_records_with_a_grant() -> None:
    """The happy path: a caller holding `listings:export` is accepted. The route writes nothing
    itself — there is nothing more to assert here than that it does not refuse."""
    body = exports.ExportRecordIn(listing="clients", format="pdf", rows=42)
    assert await exports.record_export(body, principal=_principal()) is None


async def test_refuses_a_caller_without_listings_export() -> None:
    """A crafted POST without the grant 403s rather than being recorded — the same gate the
    listing-export routes already enforce for CSV and Excel."""
    body = exports.ExportRecordIn(listing="clients", format="csv", rows=0)
    with pytest.raises(HTTPException) as err:
        await exports.record_export(body, principal=_principal("contacts:read"))
    assert err.value.status_code == 403
    assert "listings:export" in str(err.value.detail)


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "pdf"])
def test_subject_captures_the_listing_and_format(fmt: str) -> None:
    """`ActionEventsMiddleware` reads the request independently of the handler — this is what it
    would capture into `metadata.subject` for each of the three export formats."""
    body: dict[str, Any] = {"listing": "account-transactions", "format": fmt, "rows": 7}
    assert (
        audit_actions.extract_subject("export.listing_exported", json.dumps(body).encode(), {})
        == body
    )
