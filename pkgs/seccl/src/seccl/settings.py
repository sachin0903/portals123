"""Seccl API connection settings.

Moved here from `services/seccl` so the API can reach Seccl the way it already reaches
Morgan Lloyd and SEI -- through a PACKAGE. `seccl_sync` re-exports this, so there is still
exactly one definition: the client that was validated against the live API, not a second
one written for the admin console.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class SecclSettings(BaseSettings):
    """Seccl API connection + auth. All vars are ``SECCL_*``."""

    # BOTH files, later wins. `.env` alone is right for `services/seccl` and wrong for the API,
    # whose secrets live in `.env.local` by convention — the exact trap `MorganLloydSettings` was
    # in, where credentials placed where `.env.example` says to put them were read by nothing and
    # the failure surfaced only as a 401 from a live vendor. Fixed here on the way past rather
    # than waiting for it to happen a second time.
    model_config = SettingsConfigDict(
        env_prefix="SECCL_", env_file=(".env", ".env.local"), extra="ignore"
    )

    api_url: str = "https://pfolio-api-staging.seccl.tech"
    firm_id: str = ""
    # private_key_jwt is production Seccl (Auth0 M2M with a registered public key);
    # client_secret covers staging Auth0 client-credentials; legacy is the
    # deprecated /authenticate endpoint.
    auth_type: Literal["private_key_jwt", "client_secret", "legacy"] = "private_key_jwt"
    auth0_domain: str = ""  # e.g. https://seccl-prod-selct.uk.auth0.com/
    audience: str = ""  # OAuth audience; defaults to api_url when blank
    client_id: str = ""
    kid: str = ""  # key id of the public key registered with Seccl
    private_key_b64: str = ""  # base64-encoded PEM; takes precedence over the path
    private_key_path: str = ""  # PEM used to sign client assertions
    client_secret: str = ""
    user_id: str = ""  # legacy auth only
    password: str = ""  # legacy auth only
    webhook_secret: str = ""  # HMAC secret shared with Seccl's outgoing webhooks
    webhook_url: str = ""  # public base URL of the gateway, for webhook registration
    page_size: int = 500
    # Sets no bigger than this are refetched as ONE exactly-sized page: Seccl's
    # page-boundary ordering is unstable (rows duplicate/vanish across pages),
    # and a single page is the only exact read. Bigger sets page + dedupe.
    single_page_cap: int = 5000
    concurrency: int = 8
    # Documents fetched per raw.files run; 0 = no cap. The first pull for an
    # established firm is thousands of PDFs, so a bounded first run is often
    # wanted — the anti-join means the rest follow on the next passes.
    files_max_per_run: int = 0

    @property
    def oauth_audience(self) -> str:
        return self.audience or self.api_url
