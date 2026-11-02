"""The composed SEI client: transport core + every endpoint section."""

from __future__ import annotations

from sei.endpoints import (
    AccountsApi,
    ActivitiesApi,
    CashApi,
    CashReceiptsApi,
    ClientsApi,
    EnrollmentApi,
    FeesApi,
    HoldingsApi,
    InvestmentModelsApi,
    ReferenceApi,
    TransfersApi,
)


class SeiClient(
    ReferenceApi,
    EnrollmentApi,
    ClientsApi,
    AccountsApi,
    CashReceiptsApi,
    CashApi,
    InvestmentModelsApi,
    TransfersApi,
    FeesApi,
    HoldingsApi,
    ActivitiesApi,
):
    """Authenticated access to SEI's Wealth Platform (SWP) API. Use as an async context
    manager:

    .. code-block:: python

        from sei import SeiClient, SeiSettings

        async with SeiClient(SeiSettings.for_environment("i31")) as api:
            templates = await api.account_templates(3102)
            result = await api.create_currency_receipt(attributes)

    Entering the context validates configuration and loads the mTLS identity, so a missing
    credential raises :class:`sei.SeiConfigError` before any network call is attempted.

    The mixin order is the order an operation happens in — reference data, then enrolment,
    then the client and account, then money, then the things that follow money. It has no
    functional effect (no method name is defined twice) and is kept as documentation.
    """
