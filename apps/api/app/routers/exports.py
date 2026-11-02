"""Recording that a listing left the platform as a file.

CSV, Excel and PDF listing exports are all built by the portals, off the listing's own reads under
their own RLS: the portal's server writes the CSV and the workbook, and for a PDF the browser draws
the table with jsPDF (`pkgs/ui`'s `table-pdf.ts`; this replaced the old `POST /exports/table.pdf`
render-worker round trip). Nothing about producing the file touches this API any more.

What a portal-built file CANNOT do is put a row in the audit trail: `ActionEventsMiddleware`
(app/audit_actions.py) only ever sees HTTP the API handles. So this route performs no export
itself — it exists to BE a mutating request the middleware can record. One call, after the file is
already built, naming the format and the row count; the middleware does the actual writing.

The gate is the same `listings:export` the three listing-export routes (and the old PDF endpoint)
already enforced: taking the book off-platform is the material act, not which format carries it.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.auth import Principal, get_current_principal, require_permission
from app.permissions import LISTINGS_EXPORT

router = APIRouter(prefix="/exports", tags=["exports"])


class ExportRecordIn(BaseModel):
    listing: str = Field(pattern=r"^[a-z][a-z-]{0,39}$")
    """Which listing was exported — a short code (`clients`, `accounts`, `transactions`,
    `client-transactions`, `account-transactions`, `holdings`), never a free-text label."""
    format: Literal["csv", "xlsx", "pdf"]
    rows: int = Field(ge=0)


@router.post("/record", name="record-export", status_code=status.HTTP_204_NO_CONTENT)
async def record_export(
    body: ExportRecordIn,
    principal: Principal = Depends(get_current_principal),
) -> None:
    """Authorise and accept. The body exists to be captured as the event's subject
    (`metadata.subject`) by the middleware, which reads the request independently — this handler
    writes nothing itself."""
    require_permission(principal, LISTINGS_EXPORT)
