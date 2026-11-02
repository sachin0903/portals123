"""Morgan Lloyd (Clifton Asset Management) CRM API client.

The API is a Microsoft Dynamics 365 instance fronted by custom actions
(``cam_*``) plus standard OData entity reads; docs: https://dev.api.morgan-lloyd.co.uk/.
Option-set integers ("magic values") are transcribed into :mod:`morgan_lloyd.enums`.
"""

from morgan_lloyd._base import (
    MorganLloydActionError,
    MorganLloydApiError,
    MorganLloydAuthError,
    MorganLloydError,
)
from morgan_lloyd.client import MorganLloydClient
from morgan_lloyd.settings import MorganLloydSettings

__all__ = [
    "MorganLloydActionError",
    "MorganLloydApiError",
    "MorganLloydAuthError",
    "MorganLloydClient",
    "MorganLloydError",
    "MorganLloydSettings",
]
