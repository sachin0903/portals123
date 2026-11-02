"""Runtime settings. Instantiate lazily (inside functions) — model discovery
imports every module under models/, which must succeed with no env configured."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SeiSettings(BaseSettings):
    """SEI SFTP drop, PGP key material and the core object store. All vars are ``SEI_*``."""

    model_config = SettingsConfigDict(
        env_prefix="SEI_",
        env_file=".env",
        extra="ignore",
        # `validation_alias` REPLACES the field name for construction, so without this a caller
        # that builds these by keyword stops working the moment a field gains a fallback alias.
        populate_by_name=True,
    )

    # --- SFTP ---
    sftp_host: str = "gws.ftp.seic.com"
    sftp_port: int = 22
    sftp_username: str = ""
    sftp_password: str = ""
    # Blank means "the login home directory" — SEI drops into the account home.
    sftp_remote_dir: str = ""
    sftp_timeout_seconds: int = 30
    # Which SEI environment the credentials belong to. Tags the raw object keys so
    # an integration drop can never be mistaken for a production one.
    env: Literal["i31", "i10", "i04", "i03", "p04"] = "i31"

    # A directory of drop files to read *instead of* the SFTP drop. The CLI has
    # --local-dir for this; the setting exists so `raw.files` can be exercised off the
    # SEI allowlist too, since a model takes no arguments of its own. Blank = live SFTP.
    local_drop_dir: str = ""

    # --- SWP REST API (the second source; docs/sei.md §1a) -----------------------
    # The SFTP drop is a pure DAILY DELTA — each file holds only that day's rows — so a
    # gap in the drops is a permanent hole in history. The API is how a hole gets filled,
    # and it is a per-account drill-down rather than a bulk feed (see api.py).
    #
    # mTLS client identity (SEI issues the pfx) + OAuth password grant. Blank
    # api_base_url disables every API model, so the SFTP pipeline runs unchanged.
    api_base_url: str = ""
    oauth_username: str = ""
    oauth_password: str = ""
    appkey: str = ""
    appsecret: str = ""
    # mTLS client identity, base64 of the PKCS#12 (`base64 -w0 sei.pfx`).
    #
    # THE ONLY FORM. A `SEI_PFX_PATH` fallback existed until Aug 2026 and is gone — a
    # deployment sets one secret rather than mounting a file, nothing keeps a certificate on
    # disk beside the code, and two forms meant two precedence rules to keep in step with
    # `pkgs/sei` (they had already drifted). The PGP key deliberately keeps its path fallback:
    # it is developer-facing and a 9KB armoured key is awkward to paste.
    pfx_base64: str = ""
    pfx_password: str = ""
    # ``GET /employeeservice/firmemployeeusers`` requires ``serviceId``. The OpenAPI example is
    # 152; on i31 that returns 0 rows. Empirically service 1 is a superset of every other
    # populated serviceId in 1–250 (docs/gotchas.md). Not a DSR tag.
    service_id: str = "1"
    # Days behind the cursor an incremental run re-requests. The default is 0: the next
    # run asks only for days after the last one it covered. SEI has no last-modified, so a
    # correction to an older transaction date is not selected unless this overlap is set.
    # `merge` on transaction_id absorbs a day that is read again.
    api_lookback_days: int = 0
    api_concurrency: int = 4
    api_timeout_seconds: int = 60

    # --- PGP ---
    pgp_private_key_base64: str = ""
    pgp_private_key_path: str = ""
    pgp_passphrase: str = ""

    # --- shared object store ------------------------------------------------------------------
    # SHARED with every other service, not this provider's own: one core bucket, one credential,
    # one endpoint. Each field accepts its `SEI_*` name FIRST (so a provider can still be pointed
    # somewhere else) and falls back to the CANONICAL `S3_*` name the API and the compose stack
    # already set. Before this, one value had to be repeated in three service `.env` files — and a
    # wrong endpoint does not raise: the parquet lands where the reader never looks, so it reads as
    # "the catalogue is empty" rather than as a misconfiguration.
    core_bucket: str = Field(
        "platforms", validation_alias=AliasChoices("SEI_CORE_BUCKET", "S3_CORE_BUCKET")
    )
    s3_endpoint: str = Field(
        "", validation_alias=AliasChoices("SEI_S3_ENDPOINT", "S3_ENDPOINT_HOST")
    )
    s3_access_key_id: str = Field(
        "", validation_alias=AliasChoices("SEI_S3_ACCESS_KEY_ID", "S3_ACCESS_KEY_ID")
    )
    s3_secret_access_key: str = Field(
        "", validation_alias=AliasChoices("SEI_S3_SECRET_ACCESS_KEY", "S3_SECRET_ACCESS_KEY")
    )
    s3_region: str = Field("us-east-1", validation_alias=AliasChoices("SEI_S3_REGION", "S3_REGION"))

    @property
    def endpoint_url(self) -> str:
        """boto3 wants a scheme; the interlace secret wants bare host[:port]."""
        if not self.s3_endpoint:
            return ""
        if self.s3_endpoint.startswith(("http://", "https://")):
            return self.s3_endpoint
        return f"http://{self.s3_endpoint}"

    def pfx_material(self) -> bytes:
        """The PKCS#12 bytes.

        One form, so one failure: an unset or malformed value is a NAMED error here rather than
        an opaque TLS handshake failure hundreds of requests later.
        """
        if not self.pfx_base64:
            raise RuntimeError("no SEI client certificate configured: set SEI_PFX_BASE64")
        try:
            return base64.b64decode(self.pfx_base64, validate=True)
        except binascii.Error as exc:
            raise RuntimeError("SEI_PFX_BASE64 is not valid base64 of a PKCS#12") from exc

    def private_key_armour(self) -> str:
        """The armoured PGP private key, base64 var first then the file fallback.

        Raises RuntimeError rather than returning a partial key — a wrong key here
        surfaces as an opaque pgpy failure hundreds of files later.
        """
        if self.pgp_private_key_base64:
            try:
                return base64.b64decode(self.pgp_private_key_base64, validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError) as exc:
                raise RuntimeError(
                    "SEI_PGP_PRIVATE_KEY_BASE64 is not valid base64 of an armoured PGP key"
                ) from exc
        if self.pgp_private_key_path:
            path = Path(self.pgp_private_key_path).expanduser()
            if not path.is_file():
                raise RuntimeError(f"SEI_PGP_PRIVATE_KEY_PATH does not exist: {path}")
            return path.read_text(encoding="utf-8")
        raise RuntimeError(
            "no PGP private key configured: set SEI_PGP_PRIVATE_KEY_BASE64 "
            "(preferred) or SEI_PGP_PRIVATE_KEY_PATH"
        )
