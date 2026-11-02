"""SEI Wealth Platform (SWP) API client.

SEI publishes SWP as ~200 separate Swagger files; `pkgs/sei/spec/sei.openapi.json` is the
ONE serialisation of the merged result — a `sei.openapi.yaml` declaring the identical document
(same title, same `merged-2026-06-26` version) sat beside it with no consumer, costing 7.4 MB in
every clone, every moon input hash and every diff, and giving two artefacts that could silently
disagree after the next regeneration. It is the
merged form (265 operations). This package covers the subset Graphene calls, grouped by
capability rather than by service — see :mod:`sei.endpoints`.

**Read the nightly SFTP drop first.** `services/sei` pulls a 47-report drop that carries the
book and a 183-code-set Reference dimension, and where the drop has something this API also
has, the drop is the better source: complete, free, already conformed, and not rate-limited.
This client is for the three things the drop cannot do — WRITE, read back a write before the
next drop exists, and fetch the handful of firm-configured ids no report carries (enrolment
templates above all).

The transport is the part that generalises: mTLS + an OAuth password grant + an AppKey header,
all three required, plus a source-IP allowlist at SEI's edge.
"""

from sei._base import (
    SeiApiError,
    SeiAuthError,
    SeiConfigError,
    SeiEntitlementError,
    SeiError,
    clear_token_cache,
    ssl_context,
)
from sei.client import SeiClient
from sei.models import (
    Attributes,
    CurrencyReceiptRequest,
    Daily,
    Monthly,
    Once,
    ProcessingScheduleDetails,
    ReceiptInstructions,
    ReceiptResult,
    Recurring,
    Weekly,
    money,
)
from sei.settings import ENVIRONMENTS, SeiSettings

__all__ = [
    "ENVIRONMENTS",
    "Attributes",
    "CurrencyReceiptRequest",
    "Daily",
    "Monthly",
    "Once",
    "ProcessingScheduleDetails",
    "ReceiptInstructions",
    "ReceiptResult",
    "Recurring",
    "SeiApiError",
    "SeiAuthError",
    "SeiEntitlementError",
    "SeiClient",
    "SeiConfigError",
    "SeiError",
    "SeiSettings",
    "Weekly",
    "clear_token_cache",
    "money",
    "ssl_context",
]
