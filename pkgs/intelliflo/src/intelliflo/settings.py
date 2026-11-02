"""Runtime settings. Instantiate lazily (inside functions) so importing the
package succeeds with no environment configured."""

from __future__ import annotations

import base64

from pydantic_settings import BaseSettings, SettingsConfigDict


class IntellifloSettings(BaseSettings):
    """Intelliflo Office API connection + auth. All vars are ``INTELLIFLO_*``.

    Server-to-server auth is an OAuth2 client-credentials grant against Intelliflo's identity
    server (``token_url``); the adviser-directory read itself carries a second, separate
    credential (``x-api-key``). Intelliflo issue the client_id / client_secret / scope /
    tenant_id / api_key per integrating firm.
    """

    # BOTH files, later wins -- the same convention `morgan_lloyd.settings` fixed for itself.
    # `apps/api`'s local secrets live in `.env.local`; a bare `env_file=".env"` would leave
    # `INTELLIFLO_CLIENT_ID` / `_CLIENT_SECRET` placed exactly where `.env.example` says to put
    # them read by nothing.
    model_config = SettingsConfigDict(
        env_prefix="INTELLIFLO_", env_file=(".env", ".env.local"), extra="ignore"
    )

    client_id: str = ""
    client_secret: str = ""
    grant_type: str = "client_credentials"
    scope: str = ""
    tenant_id: str = ""
    base_url: str = ""
    api_key: str = ""
    token_url: str = "https://identity.gb.intelliflo.net/core/connect/token"  # noqa: S105 - a URL, not a credential
    timeout: float = 30.0

    @property
    def basic_auth(self) -> str:
        """The token endpoint's ``Authorization: Basic`` value: ``client_id:client_secret``,
        base64-encoded."""
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return base64.b64encode(raw).decode()
