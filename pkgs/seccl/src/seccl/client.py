"""The composed Seccl client: transport core + every endpoint section.

``SecclApiError`` and ``SecclClient`` are re-exported here because that is where every existing
importer looks for them (`apps/api/app/platform_actions.py`,
`services/seccl/src/seccl_sync/client.py`, and through the latter every sync model). The
transport moved to :mod:`seccl._base` when the endpoint mixins were added; nothing that imported
from `seccl.client` has to change.
"""

from __future__ import annotations

from seccl._base import BaseClient, SecclApiError
from seccl.endpoints import (
    ClientsApi,
    FeesApi,
    IllustrationsApi,
    ModelsApi,
    PensionsApi,
    TransactionsApi,
)

__all__ = ["BaseClient", "SecclApiError", "SecclClient"]


class SecclClient(ClientsApi, TransactionsApi, ModelsApi, PensionsApi, FeesApi, IllustrationsApi):
    """Authenticated access to the Seccl API. Use as an async context manager:

    .. code-block:: python

        from seccl import SecclClient

        async with SecclClient() as api:
            client = await api.client("0123ABC")
            accounts = await api.accounts_for_client(client["id"])

    The generic reads the sync services rely on (``get``, ``get_record``, ``get_records``,
    ``get_list``, ``get_many``, ``file_url``, ``download``) are on the transport base and stay
    available: they are what pages a whole book, and an endpoint method per collection would be
    a worse interface for that job.
    """
