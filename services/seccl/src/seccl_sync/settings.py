"""Runtime settings for the sync SERVICE. Instantiate lazily (inside functions) — model
discovery imports every module under models/, which must succeed with no env configured.

The API connection settings live in `pkgs/seccl` and are re-exported here."""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ONE definition, re-exported. `SecclSettings` moved to `pkgs/seccl` so the API can reach
# Seccl through a package rather than depending on a sync service; every `seccl_sync`
# importer keeps working, and there is no second copy to drift.
from seccl.settings import SecclSettings


class ObjectStoreSettings(BaseSettings):
    """Where client documents land. Reuses the ``SECCL_S3_*`` credentials that already
    address the core bucket for the warehouse's parquet."""

    model_config = SettingsConfigDict(
        env_prefix="SECCL_S3_",
        env_file=".env",
        extra="ignore",
        # `validation_alias` REPLACES the field name for construction, so without this a caller
        # (or a test) that builds these by keyword — `ObjectStoreSettings(endpoint="h:9000")` —
        # stops working the moment a field gains a fallback alias.
        populate_by_name=True,
    )

    # --- shared object store ------------------------------------------------------------------
    # SHARED with every other service, not this provider's own: one core bucket, one credential,
    # one endpoint. Each field accepts its `SECCL_S3_*` name FIRST (so a provider can still be
    # pointed somewhere else) and falls back to the CANONICAL `S3_*` name the API and the stack
    # already set. Before this, one value had to be repeated in three service `.env` files — and a
    # wrong endpoint does not raise: the parquet lands where the reader never looks, so it reads as
    # "the catalogue is empty" rather than as a misconfiguration.
    # host[:port], no scheme (matches the interlace secret's form)
    endpoint: str = Field(
        "", validation_alias=AliasChoices("SECCL_S3_ENDPOINT", "S3_ENDPOINT_HOST")
    )
    access_key_id: str = Field(
        "", validation_alias=AliasChoices("SECCL_S3_ACCESS_KEY_ID", "S3_ACCESS_KEY_ID")
    )
    secret_access_key: str = Field(
        "", validation_alias=AliasChoices("SECCL_S3_SECRET_ACCESS_KEY", "S3_SECRET_ACCESS_KEY")
    )
    use_ssl: bool = False
    region: str = Field("us-east-1", validation_alias=AliasChoices("SECCL_S3_REGION", "S3_REGION"))
    # Documents are provider-raw, so they sit in the core bucket's raw zone
    # alongside the untransformed provider data (docs/storage.md).
    bucket: str = Field(
        "platforms", validation_alias=AliasChoices("SECCL_S3_BUCKET", "S3_CORE_BUCKET")
    )
    prefix: str = "raw/seccl/files"

    @property
    def endpoint_url(self) -> str:
        scheme = "https" if self.use_ssl else "http"
        return f"{scheme}://{self.endpoint}"


class GatewaySettings(BaseSettings):
    """Where the webhook gateway publishes enriched events. Vars are ``GATEWAY_*``."""

    model_config = SettingsConfigDict(env_prefix="GATEWAY_", env_file=".env", extra="ignore")

    interlace_url: str = "http://127.0.0.1:8100"
    interlace_api_key: str = ""  # ilk_ key with write scope; blank for an open local instance


__all__ = ["GatewaySettings", "ObjectStoreSettings", "SecclSettings"]
