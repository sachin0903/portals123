"""Deployment-capability and global reference reads (``/ref/*``) — no tenant secrets.

Capability answers ("what can this deployment do") are identical for every tenant and derived from
configuration alone. Global reference rows (``ref.currencies``) are facts that predate any firm
(0050_ref_data) — readable by every authenticated principal, never scoped by tenant.

Authenticated (any signed-in portal user, as ``ref/postcoder``): the facts are not confidential, but
an unauthenticated route is a fingerprint of the deployment's integrations for anyone who asks.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal, get_current_principal
from app.complycube.client import complycube_configured
from app.countries import catalogue as country_catalogue
from app.currencies import catalogue
from app.db import target_tenant_session as tenant_session
from app.idv import manual as idv_manual

router = APIRouter(prefix="/ref", tags=["ref"])


@router.get("/idv", name="idv-availability")
async def idv_availability(
    _principal: Principal = Depends(get_current_principal),
) -> dict[str, Any]:
    """How this deployment can verify an identity: digitally (a ComplyCube key is set), and what
    the MANUAL path collects.

    ``available`` is the same test the onboarding seed uses to choose ``provider: complycube`` over
    ``manual`` (``flows/onboarding_seed._idv_provider``), so the kickoff cannot describe a path the
    seeded flow will not take. ``manual_documents`` is always present, because a manual check is
    always possible — and the adviser choosing it at kickoff needs to see what it will ask the
    client for BEFORE the journey starts, from the same definition the client is later shown
    (``app/idv/manual.py``).
    """
    return {"available": complycube_configured(), "manual_documents": idv_manual.as_json()}


@router.get("/currencies", name="list-currencies")
async def list_currencies(
    product_id: int | None = None,
    session: AsyncSession = Depends(tenant_session),
    _principal: Principal = Depends(get_current_principal),
) -> list[dict[str, Any]]:
    """The currencies allowed by the firm and, when supplied, the selected product.

    The firm's base is the default when the product permits it; otherwise the product's first
    configured currency is. This makes a GBP-only ISA default to GBP even for a EUR-base firm.

    Symbols and names come from the in-memory ``ref.currencies`` catalogue. The allow-list is
    the firm's, and that is the only part of this response that can change.
    """
    policy = (
        (
            await session.execute(
                text(
                    "SELECT t.base_currency, CASE "
                    "    WHEN CAST(:product AS bigint) IS NULL THEN t.allowed_currencies "
                    "    ELSE COALESCE(p.allowed_currencies, ARRAY[]::text[]) END AS allowed "
                    "  FROM auth.tenants t "
                    "  LEFT JOIN platform.products p "
                    "    ON p.id = CAST(:product AS bigint) AND p.tenant_id = t.id "
                    "  WHERE t.id = auth.tenant_id()"
                ),
                {"product": product_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if policy is None:
        return []
    allowed = [str(code) for code in (policy["allowed"] or [])]
    base = str(policy["base_currency"] or "")
    # Postgres arrays are 1-indexed; `allowed[1]` is the first stored member, not the
    # alphabetically first. That is the default when the firm's base is not permitted.
    effective = base if base in allowed else (allowed[0] if allowed else None)
    ref = await catalogue()
    rows = []
    for code in allowed:
        item = ref.get(code)
        if item is None:
            continue
        rows.append({**item.as_ref(), "is_base": item.code == effective})
    rows.sort(key=lambda row: (not row["is_base"], row["code"]))
    return rows


@router.get("/countries", name="list-countries")
async def list_countries(
    _principal: Principal = Depends(get_current_principal),
) -> list[dict[str, Any]]:
    """Countries and their identifier rules for fact-find fields.

    Nationality labels, tax residence and national-identifier selection must all read the same
    reference rows. Returning ``identifiers`` here is what lets the form choose NI number,
    passport or CONCAT from nationality instead of exposing identifier type as adviser-entered
    text.

    The rows are the process cache of ``ref.countries``. The table is seed data, so this
    route does not query it.
    """
    ref = await country_catalogue()
    return [row.as_ref() for row in ref.ordered()]
