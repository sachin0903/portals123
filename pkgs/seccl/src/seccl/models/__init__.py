"""Generated Seccl payload models -- see models/<resource>.py.

Regenerate with `moon run seccl:build-schemas && moon run seccl:generate-models`.
Nested types (BankDetails, AuditDetails, ...) live in their resource's module: the
same name means different things across resources, so they are not re-exported.
"""

from __future__ import annotations

from seccl.models.account import Account, AccountCreate, AccountUpdate
from seccl.models.asset import Asset, AssetCreate
from seccl.models.client import Client, ClientCreate, ClientUpdate
from seccl.models.clientproduct import ClientProduct, ClientProductCreate, ClientProductUpdate
from seccl.models.event import Event, EventCreate
from seccl.models.files import File, FileCreate
from seccl.models.illustration import Illustration, IllustrationCreate
from seccl.models.model import ModelCreate
from seccl.models.portfoliogroup import PortfolioGroup, PortfolioGroupCreate, PortfolioGroupUpdate
from seccl.models.portfoliotransaction import (
    PortfolioTransaction,
    PortfolioTransactionCreate,
    PortfolioTransactionUpdate,
)
from seccl.models.portfoliotransactionaction import (
    PortfolioTransactionAction,
    PortfolioTransactionActionUpdate,
)
from seccl.models.portfoliotransactiongroup import (
    PortfolioTransactionGroup,
    PortfolioTransactionGroupCreate,
)
from seccl.models.webhook import Webhook, WebhookCreate, WebhookUpdate

__all__ = [
    "Account",
    "AccountCreate",
    "AccountUpdate",
    "Asset",
    "AssetCreate",
    "Client",
    "ClientCreate",
    "ClientUpdate",
    "ClientProduct",
    "ClientProductCreate",
    "ClientProductUpdate",
    "Event",
    "EventCreate",
    "File",
    "FileCreate",
    "Illustration",
    "IllustrationCreate",
    "ModelCreate",
    "PortfolioGroup",
    "PortfolioGroupCreate",
    "PortfolioGroupUpdate",
    "PortfolioTransaction",
    "PortfolioTransactionCreate",
    "PortfolioTransactionUpdate",
    "PortfolioTransactionAction",
    "PortfolioTransactionActionUpdate",
    "PortfolioTransactionGroup",
    "PortfolioTransactionGroupCreate",
    "Webhook",
    "WebhookCreate",
    "WebhookUpdate",
]
