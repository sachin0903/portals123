"""The composed Morgan Lloyd client: transport core + every endpoint section."""

from __future__ import annotations

from morgan_lloyd.endpoints import (
    BenefitsApi,
    ContactsApi,
    FactfindsApi,
    SchemesApi,
    TransfersApi,
    ValuationsApi,
)


class MorganLloydClient(
    ContactsApi, FactfindsApi, SchemesApi, TransfersApi, BenefitsApi, ValuationsApi
):
    """Authenticated access to the Morgan Lloyd (Clifton Asset Management)
    Dynamics 365 CRM API. Use as an async context manager:

    .. code-block:: python

        async with MorganLloydClient() as api:
            contacts = await api.get_all_contacts()

    Endpoint methods take/return plain dicts keyed by the CRM's field names;
    option-set magic values live in :mod:`morgan_lloyd.enums`.
    """
