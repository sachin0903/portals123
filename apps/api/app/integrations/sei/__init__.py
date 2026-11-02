"""SEI Wealth Platform access for the API.

A thin seam over ``pkgs/sei`` so routers never import the vendor client directly and
"is it configured?" is answered in one place.
"""

from app.integrations.sei import reference
from app.integrations.sei.cash_receipts import (
    CONFIG_DESCRIPTION_CODE,
    CONFIG_FIRM_ID,
    CONFIG_PLATFORM_USER,
    CONFIG_TRANSFER_DESCRIPTION_CODE,
    CONFIG_TRANSFER_TREATMENT_CODE,
    CONFIG_TREATMENT_CODES,
    SeiNotConfigured,
    SeiUnavailable,
    build_receipt,
    configured,
    create_anticipated_receipt,
    create_transfer_receipt,
    explain_missing_transfer,
    missing_fields,
    missing_transfer_fields,
    resolve_codes,
    resolve_transfer_codes,
)
from app.integrations.sei.reference import (
    describe,
    is_relief_payment,
    suggested_category,
)

__all__ = [
    "CONFIG_DESCRIPTION_CODE",
    "CONFIG_FIRM_ID",
    "CONFIG_PLATFORM_USER",
    "CONFIG_TRANSFER_DESCRIPTION_CODE",
    "CONFIG_TRANSFER_TREATMENT_CODE",
    "CONFIG_TREATMENT_CODES",
    "SeiNotConfigured",
    "SeiUnavailable",
    "build_receipt",
    "configured",
    "create_anticipated_receipt",
    "create_transfer_receipt",
    "describe",
    "explain_missing_transfer",
    "is_relief_payment",
    "missing_fields",
    "missing_transfer_fields",
    "reference",
    "resolve_codes",
    "resolve_transfer_codes",
    "suggested_category",
]
