"""Endpoint mixins, one module per capability we exercise at SEI.

Deliberately **not** one module per SWP service: SEI publishes ~200 services and the merged
spec carries 265 operations, most of which are trading, corporate actions, tax and back-office
plumbing we have no use for. The modules here are grouped by what a firm is trying to do —
open an account, move cash, attach a model, follow an instruction — because that is the
question a caller arrives with.

Every module ships GETs alongside its writes. That is not symmetry for its own sake: SEI's
reporting path is the nightly SFTP drop, so without a read there is no way to confirm a write
landed until tomorrow, and "did that work?" is the question that matters most on a money
instruction.
"""

from sei.endpoints.accounts import AccountsApi
from sei.endpoints.activities import ActivitiesApi
from sei.endpoints.cash import CashApi
from sei.endpoints.cash_receipts import CashReceiptsApi
from sei.endpoints.clients import ClientsApi
from sei.endpoints.enrollment import EnrollmentApi
from sei.endpoints.fees import FeesApi
from sei.endpoints.holdings import HoldingsApi
from sei.endpoints.investment_models import InvestmentModelsApi
from sei.endpoints.reference import ReferenceApi
from sei.endpoints.transfers import TransfersApi

__all__ = [
    "AccountsApi",
    "ActivitiesApi",
    "CashApi",
    "CashReceiptsApi",
    "ClientsApi",
    "EnrollmentApi",
    "FeesApi",
    "HoldingsApi",
    "InvestmentModelsApi",
    "ReferenceApi",
    "TransfersApi",
]
