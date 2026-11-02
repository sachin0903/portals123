"""Seccl, from the API's side.

Thin: the client, its auth, retry and pagination live in `pkgs/seccl` and were written for the
sync and validated against the live API. This module is the API's door onto them — the same
shape `integrations/morgan_lloyd` and `integrations/sei` already have — so the admin console can
send Seccl an instruction without a second client, a second set of auth decisions, or a second
opinion about what a 429 means.
"""

from __future__ import annotations

from app.integrations.seccl.connection import (
    SecclNotConfigured,
    configured,
    seccl_settings,
)

__all__ = ["SecclNotConfigured", "configured", "seccl_settings"]
