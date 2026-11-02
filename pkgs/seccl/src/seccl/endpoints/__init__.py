"""Endpoint mixins, one module per capability we exercise at Seccl.

Seccl documents 377 requests; these cover the write path a firm's book actually walks plus the
reads that confirm it. Grouped by what a caller is trying to do rather than by URL prefix,
because Seccl's prefixes cut across capabilities — `/portfoliotransactiongroup` alone is
contributions, withdrawals, transfers, fees and trades.

Every mixin composes onto :class:`seccl._base.BaseClient` and uses its audited request path, so
a new endpoint is recorded in ``audit.api_log`` the day it is written.
"""

from seccl.endpoints.clients import ClientsApi
from seccl.endpoints.fees import FeesApi
from seccl.endpoints.illustrations import IllustrationsApi
from seccl.endpoints.models import ModelsApi
from seccl.endpoints.pensions import PensionsApi
from seccl.endpoints.transactions import TransactionsApi

__all__ = [
    "ClientsApi",
    "FeesApi",
    "IllustrationsApi",
    "ModelsApi",
    "PensionsApi",
    "TransactionsApi",
]
