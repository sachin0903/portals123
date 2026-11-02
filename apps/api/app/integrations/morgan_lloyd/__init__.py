"""Morgan Lloyd (Dynamics 365) access for the API.

A thin seam over ``pkgs/morgan-lloyd`` so routers never import the vendor client directly and
"is it configured?" is answered in one place.
"""

from app.integrations.morgan_lloyd.benefits import (
    MorganLloydNotConfigured,
    MorganLloydUnavailable,
    configured,
    drawdown_illustration_template,
    drawdown_kwargs,
    drawdown_session,
    sso_url_with_policy,
)
from app.integrations.morgan_lloyd.contacts import (
    build_contact,
    client_columns,
    create_contact,
    missing_fields,
)
from app.integrations.morgan_lloyd.contributions import (
    CATEGORY_TO_ML,
    STATUS_ACTIVE,
    STATUS_APPLICATION,
    STATUS_LABELS,
    STATUS_RECEIVED,
    STATUS_STOPPED,
    UNPUSHABLE,
    NotAnApplication,
    build_contribution,
    create_contribution,
    fetch_status,
    stop_application,
    update_application,
)
from app.integrations.morgan_lloyd.contributions import (
    missing_fields as contribution_missing_fields,
)
from app.integrations.morgan_lloyd.sync import (
    LINK_POLICY,
    LINK_SCHEME,
    PENSION_PRODUCTS,
    scheme_types,
    sync_client,
)
from app.integrations.morgan_lloyd.transfers import (
    TransferNotCancellable,
    build_transfer,
    cancel_transfer,
)
from app.integrations.morgan_lloyd.transfers import (
    create_transfer as create_transfer_at_ml,
)
from app.integrations.morgan_lloyd.transfers import (
    fetch_status as fetch_transfer_status,
)
from app.integrations.morgan_lloyd.transfers import (
    missing_fields as transfer_missing_fields,
)

__all__ = [
    "CATEGORY_TO_ML",
    "LINK_POLICY",
    "LINK_SCHEME",
    "PENSION_PRODUCTS",
    "STATUS_ACTIVE",
    "STATUS_APPLICATION",
    "STATUS_LABELS",
    "STATUS_RECEIVED",
    "STATUS_STOPPED",
    "UNPUSHABLE",
    "MorganLloydNotConfigured",
    "MorganLloydUnavailable",
    "NotAnApplication",
    "TransferNotCancellable",
    "build_contact",
    "build_contribution",
    "build_transfer",
    "client_columns",
    "configured",
    "contribution_missing_fields",
    "create_contact",
    "cancel_transfer",
    "create_contribution",
    "create_transfer_at_ml",
    "drawdown_illustration_template",
    "drawdown_kwargs",
    "drawdown_session",
    "sso_url_with_policy",
    "fetch_status",
    "fetch_transfer_status",
    "missing_fields",
    "scheme_types",
    "stop_application",
    "sync_client",
    "transfer_missing_fields",
    "update_application",
]
