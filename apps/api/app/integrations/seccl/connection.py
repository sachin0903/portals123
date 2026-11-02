"""Whether this deployment can talk to Seccl, and with what.

Credentials come from `SECCL_*` env read by the vendor settings class, not from `app.config` --
one definition of the connection, shared with `services/seccl`, so the sync and the API cannot
disagree about which firm or which environment they are pointed at.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cost only
    from seccl.settings import SecclSettings


class SecclNotConfigured(RuntimeError):
    """No usable Seccl connection on this deployment. Carries what is missing."""


def seccl_settings() -> SecclSettings:
    """The connection, or a refusal naming what is absent.

    Imported lazily for the reason every vendor client here is: constructing settings at module
    load throws during the SvelteKit build's analyse step and during a test run with no
    environment, and an unconfigured deployment must still be able to IMPORT the app.
    """
    try:
        from seccl.settings import SecclSettings
    except ImportError as exc:  # pragma: no cover - only when the package is missing
        raise SecclNotConfigured("The seccl client is not installed in this environment.") from exc
    return SecclSettings()


def _missing(settings: Any) -> list[str]:
    """Which credentials this auth type needs and has not got.

    Seccl has THREE auth types and they need different things -- checking one fixed set would
    report a correctly-configured staging deployment as broken, and a production one as fine when
    it has no signing key. `auth_type` decides.
    """
    missing: list[str] = []
    if not settings.firm_id:
        missing.append("SECCL_FIRM_ID")
    if settings.auth_type == "legacy":
        if not settings.user_id:
            missing.append("SECCL_USER_ID")
        if not settings.password:
            missing.append("SECCL_PASSWORD")
        return missing
    if not settings.client_id:
        missing.append("SECCL_CLIENT_ID")
    if not settings.auth0_domain:
        missing.append("SECCL_AUTH0_DOMAIN")
    if settings.auth_type == "client_secret":
        if not settings.client_secret:
            missing.append("SECCL_CLIENT_SECRET")
    else:  # private_key_jwt
        if not settings.kid:
            missing.append("SECCL_KID")
        if not (settings.private_key_b64 or settings.private_key_path):
            missing.append("SECCL_PRIVATE_KEY_B64 or SECCL_PRIVATE_KEY_PATH")
    return missing


def configured() -> bool:
    """True when Seccl can be reached. False is a STATE, not an error -- a deployment with no
    Seccl connection is normal, and the console says so rather than offering actions that 500."""
    try:
        return not _missing(seccl_settings())
    except SecclNotConfigured:
        return False


def why_not_configured() -> str:
    """The missing variables, named. `configured()` answers whether; this answers what, because
    "Seccl is not configured" sends an operator to read code and a list does not."""
    try:
        missing = _missing(seccl_settings())
    except SecclNotConfigured as exc:
        return str(exc)
    if not missing:
        return ""
    return "Seccl is unavailable for this firm. Contact support."
