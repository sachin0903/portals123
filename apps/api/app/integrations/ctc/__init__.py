"""CTC illustration engine (Bespoke XML v1.6). No credentials — see client.py."""

from app.integrations.ctc.client import (
    CtcError,
    IllustrationOutcome,
    IllustrationResult,
    generate,
    generate_or_placeholder,
)
from app.integrations.ctc.placeholder import render_placeholder_pdf
from app.integrations.ctc.xml_builder import (
    AnnuityBasis,
    CtcValidationError,
    Fee,
    FundSplit,
    IllustrationRequest,
    IllustrationResponse,
    Investor,
    PlanStream,
    PremiumSplit,
    Quotation,
    Withdrawals,
    build_request,
    parse_response,
    projection_exit_age,
)

__all__ = [
    "AnnuityBasis",
    "CtcError",
    "CtcValidationError",
    "Fee",
    "FundSplit",
    "IllustrationOutcome",
    "IllustrationRequest",
    "IllustrationResponse",
    "IllustrationResult",
    "Investor",
    "PlanStream",
    "PremiumSplit",
    "Quotation",
    "Withdrawals",
    "build_request",
    "generate",
    "generate_or_placeholder",
    "parse_response",
    "projection_exit_age",
    "render_placeholder_pdf",
]
