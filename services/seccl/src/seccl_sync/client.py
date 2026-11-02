"""The Seccl API client, re-exported.

The implementation moved to `pkgs/seccl` so `apps/api` can depend on a package rather than on a
sync service. Every `seccl_sync.client` importer here keeps working and there is no second copy:
this file is the import path, `seccl.client` is the code.
"""

from __future__ import annotations

from seccl.client import (
    SecclApiError,
    SecclClient,
)

__all__ = ["SecclApiError", "SecclClient"]
