"""Endpoint mixins, one module per API section of the Morgan Lloyd docs."""

from morgan_lloyd.endpoints.benefits import BenefitsApi
from morgan_lloyd.endpoints.contacts import ContactsApi
from morgan_lloyd.endpoints.factfinds import FactfindsApi
from morgan_lloyd.endpoints.schemes import SchemesApi
from morgan_lloyd.endpoints.transfers import TransfersApi
from morgan_lloyd.endpoints.valuations import ValuationsApi

__all__ = [
    "BenefitsApi",
    "ContactsApi",
    "FactfindsApi",
    "SchemesApi",
    "TransfersApi",
    "ValuationsApi",
]
