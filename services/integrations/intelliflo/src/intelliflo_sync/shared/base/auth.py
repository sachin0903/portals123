"""Base authentication strategies for integrations."""

import base64
import logging
import time
from abc import ABC, abstractmethod

import requests

logger = logging.getLogger(__name__)


def _error_code(response) -> str:
    """The OAuth `error` field (e.g. invalid_scope), if the body has one — never the whole body."""
    try:
        return str(response.json().get("error", ""))
    except ValueError:
        return ""


class BaseAuth(ABC):
    @abstractmethod
    def get_headers(self) -> dict: ...


class OAuth2Auth(BaseAuth):
    """OAuth2 client credentials with in-memory token caching and auto-refresh."""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        grant_type: str = "client_credentials",
        scope: str = "",
        extra_data: dict | None = None,
    ):
        self._token_url = token_url
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._grant_type = grant_type
        # Deduplicate and normalise whitespace
        seen: set[str] = set()
        deduped = []
        for s in scope.split():
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        self._scope = " ".join(deduped)
        self._extra_data = extra_data or {}
        self._access_token: str | None = None
        self._expires_at: float = 0
        self._lock = __import__("threading").Lock()

    def get_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def _get_token(self) -> str:
        with self._lock:
            if self._access_token and time.time() < self._expires_at - 60:
                return self._access_token
            self._fetch_token()
            assert self._access_token is not None  # _fetch_token always sets it or raises
            return self._access_token

    def _fetch_token(self):
        credentials = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()

        headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        data = {
            "grant_type": self._grant_type,
            "scope": self._scope,
            **self._extra_data,
        }

        # Never log the credentials, the request headers or the token response: only where we call and what
        # scopes we ask for (neither is secret).
        logger.info("Fetching OAuth2 token from %s (scope: %s)", self._token_url, self._scope)
        response = requests.post(self._token_url, headers=headers, data=data, timeout=30)
        if not response.ok:
            logger.warning(
                "OAuth2 token request failed: HTTP %s %s",
                response.status_code,
                _error_code(response),
            )
        response.raise_for_status()

        token_data = response.json()
        self._access_token = token_data["access_token"]
        self._expires_at = time.time() + token_data.get("expires_in", 3600)
        logger.info("Token fetched successfully (expires_in=%s)", token_data.get("expires_in"))


class APIKeyAuth(BaseAuth):
    def __init__(self, api_key: str, header_name: str = "x-api-key"):
        self._api_key = api_key
        self._header_name = header_name

    def get_headers(self) -> dict:
        return {self._header_name: self._api_key}


class BasicAuth(BaseAuth):
    def __init__(self, username: str, password: str):
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._header = f"Basic {encoded}"

    def get_headers(self) -> dict:
        return {"Authorization": self._header}
